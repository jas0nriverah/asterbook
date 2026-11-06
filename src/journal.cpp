#include "asterbook/journal.hpp"
#include <algorithm>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <stdexcept>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>
namespace asterbook {
namespace {
#ifdef ASTERBOOK_TEST_FAULTS
void fault(const char *stage) {
  const char *f = std::getenv("ASTERBOOK_TEST_FAULT");
  if (f && std::string(f) == stage) {
    if (std::string(stage).find("throw_") == 0)
      throw std::runtime_error("injected WAL failure");
    ::_exit(90);
  }
}
#else
void fault(const char *) {}
#endif
constexpr std::uint32_t magic = 0x31425441;
constexpr std::size_t limit = 4096;
void put32(std::string &s, std::uint32_t x) {
  for (int i = 0; i < 4; i++)
    s.push_back(char(x >> (i * 8)));
}
void put64(std::string &s, std::uint64_t x) {
  for (int i = 0; i < 8; i++)
    s.push_back(char(x >> (i * 8)));
}
std::uint32_t get32(const std::string &s, std::size_t &p) {
  if (p + 4 > s.size())
    throw std::runtime_error("short WAL field");
  std::uint32_t x = 0;
  for (int i = 0; i < 4; i++)
    x |= std::uint32_t((unsigned char)s[p++]) << (i * 8);
  return x;
}
std::uint64_t get64(const std::string &s, std::size_t &p) {
  if (p + 8 > s.size())
    throw std::runtime_error("short WAL field");
  std::uint64_t x = 0;
  for (int i = 0; i < 8; i++)
    x |= std::uint64_t((unsigned char)s[p++]) << (i * 8);
  return x;
}
void str(std::string &s, const std::string &x) {
  put32(s, static_cast<std::uint32_t>(x.size()));
  s += x;
}
std::string readstr(const std::string &s, std::size_t &p) {
  auto n = get32(s, p);
  if (n > s.size() - p)
    throw std::runtime_error("short WAL string");
  auto x = s.substr(p, n);
  p += n;
  return x;
}
std::uint32_t checksum(const std::string &s) {
  std::uint32_t h = 2166136261u;
  for (unsigned char c : s) {
    h ^= c;
    h *= 16777619u;
  }
  return h;
}
std::string encode(const Command &c) {
  std::string p;
  str(p, c.client);
  str(p, c.request);
  str(p, c.symbol);
  str(p, c.id);
  str(p, c.new_id);
  put32(p, (unsigned)c.op);
  put32(p, (unsigned)c.side);
  put32(p, (unsigned)c.tif);
  put64(p, (std::uint64_t)c.price);
  put64(p, (std::uint64_t)c.qty);
  return p;
}
Command decode(const std::string &p) {
  std::size_t i = 0;
  Command c;
  c.client = readstr(p, i);
  c.request = readstr(p, i);
  c.symbol = readstr(p, i);
  c.id = readstr(p, i);
  c.new_id = readstr(p, i);
  c.op = (Op)get32(p, i);
  c.side = (Side)get32(p, i);
  c.tif = (Tif)get32(p, i);
  c.price = (std::int64_t)get64(p, i);
  c.qty = (std::int64_t)get64(p, i);
  if (i != p.size())
    throw std::runtime_error("WAL trailing bytes");
  return c;
}
std::string readall(int fd) {
  struct stat st{};
  if (fstat(fd, &st) || st.st_size < 0)
    throw std::runtime_error("WAL stat failed");
  std::string data((std::size_t)st.st_size, '\0');
  std::size_t pos = 0;
  while (pos < data.size()) {
    ssize_t n = pread(fd, &data[pos], data.size() - pos, pos);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      throw std::runtime_error("WAL read failed");
    pos += n;
  }
  return data;
}
std::string read_at(int fd, std::uint64_t offset, std::size_t size) {
  std::string data(size, '\0');
  std::size_t pos = 0;
  while (pos < size) {
    auto n = pread(fd, &data[pos], size - pos, offset + pos);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      throw std::runtime_error("truncated WAL");
    pos += n;
  }
  return data;
}
std::uint64_t file_size(int fd) {
  struct stat st{};
  if (fstat(fd, &st) || st.st_size < 0)
    throw std::runtime_error("WAL stat failed");
  return static_cast<std::uint64_t>(st.st_size);
}
std::uint32_t prefix_checksum(int fd, std::uint64_t size) {
  std::uint32_t h = 2166136261u;
  for (std::uint64_t pos = 0; pos < size;) {
    auto data =
        read_at(fd, pos, static_cast<std::size_t>(std::min<std::uint64_t>(65536, size - pos)));
    for (unsigned char c : data) {
      h ^= c;
      h *= 16777619u;
    }
    pos += data.size();
  }
  return h;
}
std::uint64_t scan(int fd, std::uint64_t pos, std::uint64_t end, std::uint64_t ordinal,
                   Engine *engine) {
  while (pos < end) {
    if (end - pos < 12)
      throw std::runtime_error("truncated WAL header");
    auto header = read_at(fd, pos, 12);
    pos += 12;
    std::size_t p = 0;
    auto m = get32(header, p), n = get32(header, p), sum = get32(header, p);
    if (m != magic || n > limit)
      throw std::runtime_error("invalid WAL header");
    if (n > end - pos)
      throw std::runtime_error("truncated WAL record");
    auto payload = read_at(fd, pos, n);
    pos += n;
    if (checksum(payload) != sum)
      throw std::runtime_error("WAL checksum mismatch");
    p = 0;
    if (get64(payload, p) != ++ordinal)
      throw std::runtime_error("WAL ordinal mismatch");
    auto command = decode(payload.substr(p));
    if (engine) {
      auto r = engine->execute(command);
      if (r.duplicate || r.code == "HISTORY_CAPACITY" || r.code == "REQUEST_CONFLICT")
        throw std::runtime_error("invalid WAL history or configured capacity");
    }
  }
  return ordinal;
}
void writeall(int fd, const std::string &data) {
  std::size_t pos = 0;
  while (pos < data.size()) {
    auto n = ::write(fd, data.data() + pos, data.size() - pos);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      throw std::runtime_error("write failed");
    pos += n;
  }
}
} // namespace
DurableEngine::DurableEngine(const std::string &path, std::size_t live, std::size_t history,
                             const std::string &snap, AllocatorMode allocator)
    : path_(path), engine_(EngineOptions{live, history, allocator}) {
  bool created = false;
  fd_ = ::open(path.c_str(), O_RDWR | O_APPEND | (snap.empty() ? (O_CREAT | O_EXCL) : 0), 0600);
  if (fd_ >= 0)
    created = snap.empty();
  else if (errno == EEXIST)
    fd_ = ::open(path.c_str(), O_RDWR | O_APPEND);
  if (fd_ < 0)
    throw std::runtime_error("open WAL failed");
  try {
    if (flock(fd_, LOCK_EX | LOCK_NB))
      throw std::runtime_error("WAL already locked");
    auto size = file_size(fd_);
    if (size == 0) {
      if (!created)
        throw std::runtime_error("empty existing WAL");
      std::string header;
      put32(header, 0x31484241);
      put64(header, live);
      put64(header, history);
      writeall(fd_, header);
      if (fsync(fd_))
        throw std::runtime_error("WAL header sync failed");
      size = header.size();
    }
    if (size < 20)
      throw std::runtime_error("WAL version or capacity mismatch");
    auto header = read_at(fd_, 0, 20);
    std::size_t hp = 0;
    if (get32(header, hp) != 0x31484241 || get64(header, hp) != live ||
        get64(header, hp) != history)
      throw std::runtime_error("WAL version or capacity mismatch");
    if (!snap.empty()) {
      int f = ::open(snap.c_str(), O_RDONLY);
      if (f < 0)
        throw std::runtime_error("open snapshot failed");
      std::string prefix;
      try {
        prefix = readall(f);
        ::close(f);
      } catch (...) {
        ::close(f);
        throw;
      }
      std::size_t sp = 0;
      if (prefix.size() < 20 || get32(prefix, sp) != 0x31534241)
        throw std::runtime_error("invalid snapshot header");
      auto offset = get64(prefix, sp);
      auto wal_sum = get32(prefix, sp), state_sum = get32(prefix, sp);
      if (offset < 20 || offset > size || prefix_checksum(fd_, offset) != wal_sum)
        throw std::runtime_error("snapshot WAL prefix mismatch");
      auto checkpoint = prefix.substr(sp);
      if (checksum(checkpoint) != state_sum)
        throw std::runtime_error("snapshot checksum mismatch");
      auto ordinal = scan(fd_, 20, offset, 0, nullptr);
      engine_.restore(checkpoint);
      if (engine_.sequence() != ordinal)
        throw std::runtime_error("snapshot sequence mismatch");
      scan(fd_, offset, size, ordinal, &engine_);
    } else {
      scan(fd_, 20, size, 0, &engine_);
    }
    // Persist the directory entry before acknowledging records.
    auto dir = std::filesystem::path(path).parent_path();
    if (dir.empty())
      dir = ".";
    int d = ::open(dir.c_str(), O_RDONLY);
    if (d < 0 || fsync(d)) {
      if (d >= 0)
        ::close(d);
      throw std::runtime_error("WAL directory sync failed");
    }
    ::close(d);
  } catch (...) {
    ::close(fd_);
    fd_ = -1;
    throw;
  }
}
DurableEngine::~DurableEngine() {
  if (fd_ >= 0)
    ::close(fd_);
}
Result DurableEngine::execute(const Command &c) { return execute_batch({c}).front(); }
std::vector<Result> DurableEngine::execute_batch(const std::vector<Command> &commands) {
  if (poisoned_)
    throw std::runtime_error("WAL unavailable");
  if (commands.empty())
    return {};
  // Track only new request keys. Existing results remain in the engine until sync.
  std::map<std::pair<std::string, std::string>, std::string> pending;
  std::string records;
  for (const auto &c : commands) {
    if (engine_.precheck(c))
      continue;
    auto key = std::make_pair(c.client, c.request);
    if (pending.count(key))
      continue;
    if (pending.size() >= engine_.history_capacity() - engine_.sequence())
      continue;
    std::string payload;
    put64(payload, engine_.sequence() + pending.size() + 1);
    payload += encode(c);
    if (payload.size() > limit)
      throw std::runtime_error("command too large for WAL");
    put32(records, magic);
    put32(records, (std::uint32_t)payload.size());
    put32(records, checksum(payload));
    records += payload;
    pending.emplace(std::move(key), canonical_command(c));
  }
  if (pending.empty()) {
    std::vector<Result> results;
    results.reserve(commands.size());
    for (const auto &c : commands)
      results.push_back(*engine_.precheck(c));
    return results;
  }
  try {
    fault("throw_append");
    writeall(fd_, records);
    fault("after_append");
    fault("throw_sync");
    if (fsync(fd_))
      throw std::runtime_error("WAL sync failed");
    ++sync_count_;
    fault("after_sync");
    std::vector<Result> results;
    results.reserve(commands.size());
    for (const auto &c : commands)
      results.push_back(engine_.execute(c));
    fault("after_apply");
    return results;
  } catch (...) {
    poisoned_ = true;
    throw;
  }
}
void DurableEngine::snapshot(const std::string &path) const {
  if (poisoned_)
    throw std::runtime_error("WAL unavailable");
  if (std::filesystem::weakly_canonical(path) == std::filesystem::weakly_canonical(path_) ||
      (std::filesystem::exists(path) && std::filesystem::equivalent(path, path_)))
    throw std::runtime_error("snapshot cannot replace WAL");
  // The checkpoint is tied to an exact WAL prefix; the full WAL remains retained.
  auto wal_size = file_size(fd_);
  auto checkpoint = engine_.checkpoint();
  std::string data;
  put32(data, 0x31534241);
  put64(data, wal_size);
  put32(data, prefix_checksum(fd_, wal_size));
  put32(data, checksum(checkpoint));
  data += checkpoint;
  std::string tmp = path + ".tmp.XXXXXX";
  std::vector<char> buf(tmp.begin(), tmp.end());
  buf.push_back(0);
  int f = mkstemp(buf.data());
  if (f < 0)
    throw std::runtime_error("snapshot create failed");
  try {
    writeall(f, data);
    if (fsync(f))
      throw std::runtime_error("snapshot sync failed");
    if (::close(f)) {
      f = -1;
      throw std::runtime_error("snapshot close failed");
    }
    f = -1;
    if (rename(buf.data(), path.c_str()))
      throw std::runtime_error("snapshot rename failed");
    auto dir = std::filesystem::path(path).parent_path();
    if (dir.empty())
      dir = ".";
    int d = ::open(dir.c_str(), O_RDONLY);
    if (d < 0 || fsync(d)) {
      if (d >= 0)
        ::close(d);
      throw std::runtime_error("snapshot directory sync failed");
    }
    ::close(d);
  } catch (...) {
    if (f >= 0)
      ::close(f);
    unlink(buf.data());
    throw;
  }
}
} // namespace asterbook
