#include "asterbook/journal.hpp"
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
void replay(Engine &e, const std::string &data) {
  std::size_t pos = 0;
  while (pos < data.size()) {
    if (data.size() - pos < 12)
      throw std::runtime_error("truncated WAL header");
    auto m = get32(data, pos), n = get32(data, pos), sum = get32(data, pos);
    if (m != magic || n > limit)
      throw std::runtime_error("invalid WAL header");
    if (n > data.size() - pos)
      throw std::runtime_error("truncated WAL record");
    auto p = data.substr(pos, n);
    pos += n;
    if (checksum(p) != sum)
      throw std::runtime_error("WAL checksum mismatch");
    std::size_t pp = 0;
    auto ordinal = get64(p, pp);
    if (ordinal != e.sequence() + 1)
      throw std::runtime_error("WAL ordinal mismatch");
    auto r = e.execute(decode(p.substr(pp)));
    if (r.duplicate || r.code == "HISTORY_CAPACITY" || r.code == "REQUEST_CONFLICT")
      throw std::runtime_error("invalid WAL history or configured capacity");
  }
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
}
DurableEngine::DurableEngine(const std::string &path, std::size_t live, std::size_t history,
                             const std::string &snap)
    : path_(path), engine_(live, history) {
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
    auto data = readall(fd_);
    if (data.empty()) {
      if (!created)
        throw std::runtime_error("empty existing WAL");
      std::string header;
      put32(header, 0x31484241);
      put64(header, live);
      put64(header, history);
      writeall(fd_, header);
      if (fsync(fd_))
        throw std::runtime_error("WAL header sync failed");
      data = header;
    }
    std::size_t hp = 0;
    if (data.size() < 20 || get32(data, hp) != 0x31484241 || get64(data, hp) != live ||
        get64(data, hp) != history)
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
      auto wal_sum = get32(prefix, sp);
      auto state_sum = get32(prefix, sp);
      if (offset < 20 || offset > data.size() || checksum(data.substr(0, offset)) != wal_sum)
        throw std::runtime_error("snapshot WAL prefix mismatch");
      auto checkpoint = prefix.substr(sp);
      if (checksum(checkpoint) != state_sum)
        throw std::runtime_error("snapshot checksum mismatch");
      std::size_t check = 20;
      std::uint64_t ordinal = 0;
      while (check < offset) {
        if (offset - check < 12 || get32(data, check) != magic)
          throw std::runtime_error("invalid WAL prefix header");
        auto n = get32(data, check), sum = get32(data, check);
        if (n > limit || n > offset - check)
          throw std::runtime_error("invalid WAL prefix record");
        auto payload = data.substr(check, n);
        check += n;
        if (checksum(payload) != sum)
          throw std::runtime_error("WAL prefix checksum mismatch");
        std::size_t q = 0;
        if (get64(payload, q) != ++ordinal)
          throw std::runtime_error("WAL prefix ordinal mismatch");
        decode(payload.substr(q));
      }
      engine_.restore(checkpoint);
      if (engine_.sequence() != ordinal)
        throw std::runtime_error("snapshot sequence mismatch");
      replay(engine_, data.substr(offset));
    } else
      replay(engine_, data.substr(20));
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
Result DurableEngine::execute(const Command &c) {
  if (poisoned_)
    throw std::runtime_error("WAL unavailable");
  if (auto result = engine_.precheck(c))
    return *result;
  std::string p;
  put64(p, engine_.sequence() + 1);
  p += encode(c);
  if (p.size() > limit)
    throw std::runtime_error("command too large for WAL");
  std::string record;
  put32(record, magic);
  put32(record, (std::uint32_t)p.size());
  put32(record, checksum(p));
  record += p;
  // A command is acknowledged only after its WAL record is synced and applied.
  try {
    fault("throw_append");
    writeall(fd_, record);
    fault("after_append");
    fault("throw_sync");
    if (fsync(fd_))
      throw std::runtime_error("WAL sync failed");
    fault("after_sync");
  } catch (...) {
    poisoned_ = true;
    throw;
  }
  try {
    auto result = engine_.execute(c);
    fault("after_apply");
    return result;
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
  auto wal = readall(fd_);
  auto checkpoint = engine_.checkpoint();
  std::string data;
  put32(data, 0x31534241);
  put64(data, wal.size());
  put32(data, checksum(wal));
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
}
