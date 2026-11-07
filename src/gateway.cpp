#include "asterbook/gateway.hpp"
#include "asterbook/protocol.hpp"
#include "asterbook/journal.hpp"
#include "asterbook/queue.hpp"
#include "asterbook/tls.hpp"
#include <arpa/inet.h>
#include <fcntl.h>
#include <filesystem>
#include <memory>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/file.h>
#include <fstream>
#include <stdexcept>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <vector>
namespace asterbook {
namespace {
using Clock = std::chrono::steady_clock;
constexpr size_t MAX_FRAME = 4096, MAX_OUTPUT = 32 * 1024 * 1024, MAX_CLIENTS = 128;
volatile sig_atomic_t stopping = 0;
void stop_signal(int) { stopping = 1; }
bool nonblock(int fd) {
  int f = fcntl(fd, F_GETFL, 0);
  return f >= 0 && fcntl(fd, F_SETFL, f | O_NONBLOCK) == 0;
}
struct Work {
  uint64_t id = 0;
  Request request;
  Clock::time_point admitted = Clock::now();
};
struct Completion {
  uint64_t id{};
  size_t shard{};
  std::string payload;
};
struct Client {
  int fd{-1};
  uint64_t id{};
  std::shared_ptr<SSL> ssl;
  bool handshake = false;
  short tls_wait = POLLIN;
  std::string principal;
  char read_buffer[4096]{};
  Clock::time_point connected = Clock::now();
  std::string input, output;
  size_t offset = 0;
  bool inflight = false;
  size_t pending = 0;
  bool aggregate_error = false;
  std::vector<std::string> parts;
  double tokens = 100;
  Clock::time_point last = Clock::now(), activity = Clock::now(), frame_start{};
};
uint64_t hash_symbol(std::string_view s) {
  uint64_t h = 14695981039346656037ULL;
  for (unsigned char c : s) {
    h ^= c;
    h *= 1099511628211ULL;
  }
  return h;
}
bool manifest(const GatewayOptions &o) {
  const std::string path = o.data_dir + "/manifest";
  const std::string expected = "asterbook-shards-v1 fnv1a64 " + std::to_string(o.shards) + "\n";
  if (std::filesystem::exists(path)) {
    std::ifstream f(path, std::ios::binary);
    std::string data((std::istreambuf_iterator<char>(f)), {});
    if (!f || data != expected)
      throw std::runtime_error("shard manifest mismatch");
    return true;
  }
  if (o.shards != 1 && std::filesystem::exists(o.data_dir + "/journal"))
    throw std::runtime_error("cannot reshard existing journal");
  std::string tmp = path + ".tmp.XXXXXX";
  std::vector<char> name(tmp.begin(), tmp.end());
  name.push_back(0);
  int fd = mkstemp(name.data());
  if (fd < 0)
    throw std::runtime_error("manifest create failed");
  try {
    size_t p = 0;
    while (p < expected.size()) {
      ssize_t n = write(fd, expected.data() + p, expected.size() - p);
      if (n < 0 && errno == EINTR)
        continue;
      if (n <= 0)
        throw std::runtime_error("manifest write failed");
      p += size_t(n);
    }
    if (fsync(fd))
      throw std::runtime_error("manifest sync failed");
    if (close(fd)) {
      fd = -1;
      throw std::runtime_error("manifest close failed");
    }
    fd = -1;
    if (rename(name.data(), path.c_str()))
      throw std::runtime_error("manifest rename failed");
    int d = open(o.data_dir.c_str(), O_RDONLY);
    if (d < 0)
      throw std::runtime_error("manifest directory open failed");
    int rc = fsync(d);
    close(d);
    if (rc)
      throw std::runtime_error("manifest directory sync failed");
  } catch (...) {
    if (fd >= 0)
      close(fd);
    unlink(name.data());
    throw;
  }
  return false;
}
}
bool validate_gateway_options(const GatewayOptions &opt) {
  in_addr addr{};
  if (inet_pton(AF_INET, opt.bind.c_str(), &addr) != 1) {
    std::cerr << "{\"event\":\"config_error\",\"reason\":\"invalid_bind\"}\n";
    return false;
  }
  bool loopback = (ntohl(addr.s_addr) >> 24) == 127;
  TlsContext tls;
  std::set<std::string> admins;
  try {
    if (opt.insecure_local) {
      if (!loopback || !opt.tls_cert.empty() || !opt.tls_key.empty() || !opt.tls_ca.empty() ||
          !opt.admin_policy.empty() || !opt.tls_crl.empty())
        throw std::runtime_error("--insecure-local requires loopback and no TLS options");
    } else {
      if (opt.tls_cert.empty() || opt.tls_key.empty() || opt.tls_ca.empty() ||
          opt.admin_policy.empty())
        throw std::runtime_error("TLS certificate, key, client CA and admin policy are required");
      tls = make_tls_context(opt.tls_cert, opt.tls_key, opt.tls_ca, opt.tls_crl);
      admins = load_admins(opt.admin_policy);
    }
  } catch (const std::exception &e) {
    std::cerr << "{\"event\":\"config_error\",\"reason\":\"security_configuration\"}\n";
    return false;
  }
  // Non-mutating access check only; startup and writes still have to succeed.
  std::filesystem::path data(opt.data_dir);
  std::error_code ec;
  while (!data.empty() && !std::filesystem::exists(data, ec)) {
    if (ec)
      break;
    auto parent = data.parent_path();
    data = parent.empty() ? std::filesystem::path(".") : parent;
  }
  if (ec || data.empty() || !std::filesystem::is_directory(data, ec) || ec ||
      access(data.c_str(), W_OK | X_OK) != 0) {
    std::cerr << "{\"event\":\"config_error\",\"reason\":\"data_directory_access\"}\n";
    return false;
  }
  return true;
}
int run_gateway(const GatewayOptions &opt) {
  stopping = 0;
  if (!validate_gateway_options(opt))
    return 1;
  in_addr addr{};
  inet_pton(AF_INET, opt.bind.c_str(), &addr);
  TlsContext tls;
  std::set<std::string> admins;
  if (!opt.insecure_local) {
    tls = make_tls_context(opt.tls_cert, opt.tls_key, opt.tls_ca, opt.tls_crl);
    admins = load_admins(opt.admin_policy);
  }
  int listener = socket(AF_INET, SOCK_STREAM, 0);
  if (listener < 0) {
    std::cerr << "{\"event\":\"io_error\",\"operation\":\"socket\"}\n";
    return 1;
  }
  int one = 1;
  setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
  sockaddr_in sa{};
  sa.sin_family = AF_INET;
  sa.sin_addr = addr;
  sa.sin_port = htons(opt.port);
  if (bind(listener, reinterpret_cast<sockaddr *>(&sa), sizeof(sa)) < 0 ||
      listen(listener, 128) < 0 || !nonblock(listener)) {
    std::cerr << "{\"event\":\"io_error\",\"operation\":\"listen\"}\n";
    close(listener);
    return 1;
  }
  struct sigaction action{}, oldterm{}, oldint{}, oldpipe{};
  action.sa_handler = stop_signal;
  sigemptyset(&action.sa_mask);
  sigaction(SIGTERM, &action, &oldterm);
  sigaction(SIGINT, &action, &oldint);
  struct sigaction ign{};
  ign.sa_handler = SIG_IGN;
  sigemptyset(&ign.sa_mask);
  sigaction(SIGPIPE, &ign, &oldpipe);
  int wake[2] = {-1, -1};
  if (pipe(wake) < 0 || !nonblock(wake[0]) || !nonblock(wake[1])) {
    std::cerr << "{\"event\":\"io_error\",\"operation\":\"pipe\"}\n";
    if (wake[0] >= 0)
      close(wake[0]);
    if (wake[1] >= 0)
      close(wake[1]);
    close(listener);
    sigaction(SIGTERM, &oldterm, nullptr);
    sigaction(SIGINT, &oldint, nullptr);
    sigaction(SIGPIPE, &oldpipe, nullptr);
    return 1;
  }
  std::mutex cm;
  std::vector<Completion> completions;
  std::atomic<bool> fatal = false;
  std::atomic<uint64_t> processed = 0, batches = 0, wal_syncs = 0, rejected_commands = 0;
  std::array<std::atomic<uint64_t>, 32> queue_hist{}, service_hist{};
  std::array<std::atomic<uint64_t>, 16> history_usage{};
  auto observe = [](auto &hist, Clock::duration duration) {
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(duration).count();
    std::uint64_t n = ns > 0 ? static_cast<std::uint64_t>(ns) : 0;
    std::size_t bucket = 0;
    while (n > 1 && bucket + 1 < hist.size()) {
      n >>= 1;
      ++bucket;
    }
    ++hist[bucket];
  };
  std::vector<std::unique_ptr<BoundedQueue<Work>>> queues;
  std::vector<std::thread> workers;
  int lockfd = -1;
  try {
    std::filesystem::create_directories(opt.data_dir);
    {
      auto parent = std::filesystem::path(opt.data_dir).parent_path();
      if (parent.empty())
        parent = ".";
      int d = open(parent.c_str(), O_RDONLY);
      if (d < 0)
        throw std::runtime_error("data parent open failed");
      int rc = fsync(d);
      close(d);
      if (rc)
        throw std::runtime_error("data parent sync failed");
    }
    lockfd = open((opt.data_dir + "/service.lock").c_str(), O_CREAT | O_RDWR, 0600);
    if (lockfd < 0 || flock(lockfd, LOCK_EX | LOCK_NB))
      throw std::runtime_error("data directory already in use");
    bool existing = manifest(opt);
    std::vector<std::unique_ptr<DurableEngine>> engines;
    std::vector<std::string> snapshots;
    for (size_t i = 0; i < opt.shards; ++i) {
      std::string base =
          opt.shards == 1 ? opt.data_dir : opt.data_dir + "/shard-" + std::to_string(i);
      std::filesystem::create_directories(base);
      if (opt.shards > 1) {
        int d = open(opt.data_dir.c_str(), O_RDONLY);
        if (d < 0)
          throw std::runtime_error("shard parent open failed");
        int rc = fsync(d);
        close(d);
        if (rc)
          throw std::runtime_error("shard parent sync failed");
      }
      if (existing && !std::filesystem::exists(base + "/journal"))
        throw std::runtime_error("missing shard journal");
      auto snap = base + "/snapshot";
      snapshots.push_back(snap);
      engines.push_back(std::make_unique<DurableEngine>(
          base + "/journal", opt.max_live, opt.max_history,
          std::filesystem::exists(snap) ? snap : "", opt.allocator));
      history_usage[i] = engines.back()->sequence();
      queues.push_back(std::make_unique<BoundedQueue<Work>>(opt.queue_capacity));
    }
    for (size_t i = 0; i < opt.shards; ++i)
      workers.emplace_back([&, i, engine = std::move(engines[i]), snap = snapshots[i]]() mutable {
        Work w;
        while (!fatal && queues[i]->pop(w)) {
          try {
            std::vector<Work> work;
            work.push_back(std::move(w));
            if (work.front().request.kind == RequestKind::Command) {
              Work next;
              while (work.size() < opt.batch_size &&
                     queues[i]->try_pop_if(next, [](const Work &item) {
                       return item.request.kind == RequestKind::Command;
                     }))
                work.push_back(std::move(next));
            }
            auto service_start = Clock::now();
            for (const auto &item : work)
              observe(queue_hist, service_start - item.admitted);
            std::vector<std::string> outputs(work.size());
            if (work.front().request.kind == RequestKind::State)
              outputs[0] = engine->state();
            else if (work.front().request.kind == RequestKind::Ready) {
              std::string journal = opt.shards == 1
                                        ? opt.data_dir + "/journal"
                                        : opt.data_dir + "/shard-" + std::to_string(i) + "/journal";
              int fd = open(journal.c_str(), O_WRONLY);
              bool ok = fd >= 0 && fsync(fd) == 0;
              if (fd >= 0)
                close(fd);
              outputs[0] = ok ? "{\"ok\":true}" : error_json("STORAGE_UNAVAILABLE");
            } else if (work.front().request.kind == RequestKind::Checkpoint) {
              engine->snapshot(snap);
              outputs[0] = "{\"ok\":true}";
            } else {
              std::vector<Command> commands;
              std::vector<std::size_t> positions;
              auto now = Clock::now();
              for (std::size_t j = 0; j < work.size(); ++j) {
                if (now - work[j].admitted > std::chrono::milliseconds(opt.queue_timeout_ms))
                  outputs[j] = error_json("DEADLINE_EXCEEDED");
                else {
                  positions.push_back(j);
                  commands.push_back(work[j].request.command);
                }
              }
              auto before_sync = engine->sync_count();
              auto results = engine->execute_batch(commands);
              history_usage[i] = engine->sequence();
              wal_syncs += engine->sync_count() - before_sync;
              if (!commands.empty())
                ++batches;
              for (std::size_t j = 0; j < results.size(); ++j) {
                if (!results[j].accepted)
                  ++rejected_commands;
                outputs[positions[j]] = result_json(results[j]);
              }
              if (opt.shards > 1)
                for (auto &output : outputs)
                  output = "{\"shard\":" + std::to_string(i) + ",\"result\":" + output + "}";
            }
            auto service_end = Clock::now();
            for (std::size_t j = 0; j < work.size(); ++j) {
              observe(service_hist, service_end - service_start);
              if (outputs[j].size() > MAX_OUTPUT - 4) {
                if (work[j].request.kind == RequestKind::Command)
                  throw std::runtime_error("command response exceeded invariant");
                outputs[j] = error_json("RESPONSE_TOO_LARGE");
              }
              std::lock_guard lock(cm);
              completions.push_back({work[j].id, i, std::move(outputs[j])});
            }
            processed += work.size();
            char b = 1;
            (void)write(wake[1], &b, 1);
          } catch (const std::exception &e) {
            std::cerr << "{\"event\":\"fatal_error\",\"reason\":\"engine_or_storage\"}\n";
            fatal = true;
            char b = 1;
            (void)write(wake[1], &b, 1);
            break;
          }
        }
      });
  } catch (const std::exception &e) {
    if (std::string(e.what()) == "shard manifest mismatch")
      std::cerr << "{\"event\":\"startup_error\",\"reason\":\"shard manifest mismatch\"}\n";
    else
      std::cerr << "{\"event\":\"startup_error\",\"reason\":\"engine_or_storage\"}\n";
    fatal = true;
    for (auto &q : queues)
      q->close();
    for (auto &t : workers)
      t.join();
    if (lockfd >= 0)
      close(lockfd);
    close(wake[0]);
    close(wake[1]);
    close(listener);
    sigaction(SIGTERM, &oldterm, nullptr);
    sigaction(SIGINT, &oldint, nullptr);
    sigaction(SIGPIPE, &oldpipe, nullptr);
    return 1;
  }
  std::unordered_map<uint64_t, Client> clients;
  uint64_t next = 1, overload = 0, malformed = 0, rate_limited = 0, accepted = 0;
  std::cout << "{\"event\":\"listening\"}" << std::endl;
  while (!stopping && !fatal) {
    {
      std::lock_guard lock(cm);
      for (auto &c : completions) {
        auto it = clients.find(c.id);
        if (it != clients.end()) {
          auto &client = it->second;
          if (client.parts.empty()) {
            client.output = frame(c.payload);
            client.inflight = false;
          } else {
            client.parts[c.shard] = std::move(c.payload);
            if (--client.pending == 0) {
              std::string result;
              if (client.aggregate_error)
                result = error_json("OVERLOADED");
              else {
                size_t total = 64;
                for (const auto &part : client.parts) {
                  if (part.size() > MAX_OUTPUT - total - 64) {
                    total = MAX_OUTPUT;
                    break;
                  }
                  total += part.size() + 64;
                }
                if (total >= MAX_OUTPUT)
                  result = error_json("RESPONSE_TOO_LARGE");
                else {
                  result = "{\"atomic\":false,\"shards\":[";
                  for (size_t j = 0; j < client.parts.size(); ++j) {
                    if (j)
                      result += ',';
                    result +=
                        "{\"shard\":" + std::to_string(j) + ",\"result\":" + client.parts[j] + "}";
                  }
                  result += "]}";
                  if (result.size() > MAX_OUTPUT - 4)
                    result = error_json("RESPONSE_TOO_LARGE");
                }
              }
              client.parts.clear();
              client.output = frame(result);
              client.inflight = false;
            }
          }
          client.offset = 0;
        }
      }
      completions.clear();
    }
    std::vector<pollfd> pf{{listener, POLLIN, 0}, {wake[0], POLLIN, 0}};
    std::vector<uint64_t> ids;
    bool buffered_work = false;
    for (auto &[id, c] : clients) {
      if (!c.handshake && !c.inflight && c.output.empty()) {
        if (c.input.size() >= 4) {
          auto *p = reinterpret_cast<const unsigned char *>(c.input.data());
          uint32_t len = (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
                         uint32_t(p[3]);
          buffered_work |= len == 0 || len > MAX_FRAME || c.input.size() >= size_t(len) + 4;
        }
        if (c.ssl && c.tls_wait != POLLOUT && SSL_pending(c.ssl.get()) > 0)
          buffered_work = true;
      }
      short events = 0;
      if (c.ssl && (c.handshake || c.tls_wait))
        events |= c.tls_wait;
      else {
        if (!c.inflight && c.output.empty())
          events |= POLLIN;
        if (!c.output.empty())
          events |= POLLOUT;
      }
      pf.push_back({c.fd, events, 0});
      ids.push_back(id);
    }
    int n = poll(pf.data(), pf.size(), buffered_work ? 0 : 50);
    if (n < 0) {
      if (errno == EINTR)
        continue;
      std::cerr << "{\"event\":\"io_error\",\"operation\":\"poll\"}\n";
      fatal = true;
      break;
    }
    if (pf[1].revents & POLLIN) {
      char drain[256];
      while (read(wake[0], drain, sizeof(drain)) > 0) {
      }
    }
    if (pf[0].revents & POLLIN) {
      for (int batch = 0; batch < 64; ++batch) {
        int fd = accept(listener, nullptr, nullptr);
        if (fd < 0) {
          if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
            std::cerr << "{\"event\":\"io_error\",\"operation\":\"accept\"}\n";
          break;
        }
        if (clients.size() >= MAX_CLIENTS || !nonblock(fd)) {
          ++overload;
          close(fd);
          continue;
        }
        auto id = next++;
        Client client{};
        client.fd = fd;
        client.id = id;
        if (tls) {
          client.ssl.reset(SSL_new(tls.get()), SSL_free);
          if (!client.ssl || SSL_set_fd(client.ssl.get(), fd) != 1) {
            close(fd);
            continue;
          }
          SSL_set_accept_state(client.ssl.get());
          client.handshake = true;
        } else
          client.tls_wait = 0;
        clients.emplace(id, std::move(client));
        ++accepted;
      }
    }
    auto now = Clock::now();
    for (size_t i = 0; i < ids.size(); ++i) {
      auto it = clients.find(ids[i]);
      if (it == clients.end())
        continue;
      auto &c = it->second;
      bool drop = false;
      short re = pf[i + 2].revents;
      if (re & (POLLERR | POLLHUP | POLLNVAL))
        drop = true;
      if (!drop && c.handshake && (re & c.tls_wait)) {
        int rc = SSL_accept(c.ssl.get());
        if (rc == 1) {
          c.principal = peer_identity(c.ssl.get());
          if (c.principal.empty())
            drop = true;
          else {
            c.handshake = false;
            c.tls_wait = 0;
            c.activity = now;
          }
        } else {
          int e = SSL_get_error(c.ssl.get(), rc);
          if (e == SSL_ERROR_WANT_READ)
            c.tls_wait = POLLIN;
          else if (e == SSL_ERROR_WANT_WRITE)
            c.tls_wait = POLLOUT;
          else
            drop = true;
        }
      }
      if (!drop && !c.handshake && !c.output.empty() &&
          (re & (c.ssl && c.tls_wait ? c.tls_wait : POLLOUT))) {
        int sent = c.ssl ? SSL_write(c.ssl.get(), c.output.data() + c.offset,
                                     static_cast<int>(c.output.size() - c.offset))
                         : static_cast<int>(send(c.fd, c.output.data() + c.offset,
                                                 c.output.size() - c.offset, 0));
        if (sent > 0) {
          c.tls_wait = 0;
          c.offset += static_cast<size_t>(sent);
          c.activity = now;
          if (c.offset == c.output.size()) {
            c.output.clear();
            c.offset = 0;
          }
        } else if (c.ssl) {
          int e = SSL_get_error(c.ssl.get(), sent);
          if (e == SSL_ERROR_WANT_READ)
            c.tls_wait = POLLIN;
          else if (e == SSL_ERROR_WANT_WRITE)
            c.tls_wait = POLLOUT;
          else
            drop = true;
        } else if (sent == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR))
          drop = true;
      }
      bool buffered_frame = false;
      if (c.input.size() >= 4) {
        auto *header = reinterpret_cast<const unsigned char *>(c.input.data());
        uint32_t len = (uint32_t(header[0]) << 24) | (uint32_t(header[1]) << 16) |
                       (uint32_t(header[2]) << 8) | uint32_t(header[3]);
        buffered_frame = len == 0 || len > MAX_FRAME || c.input.size() >= size_t(len) + 4;
      }
      if (!drop && !c.handshake && !c.inflight && c.output.empty() && !buffered_frame &&
          ((re & (c.ssl && c.tls_wait ? c.tls_wait : POLLIN)) ||
           (c.ssl && SSL_pending(c.ssl.get()) > 0))) {
        int got = c.ssl ? SSL_read(c.ssl.get(), c.read_buffer, sizeof(c.read_buffer))
                        : static_cast<int>(recv(c.fd, c.read_buffer, sizeof(c.read_buffer), 0));
        if (got > 0) {
          c.tls_wait = 0;
          if (c.input.empty())
            c.frame_start = now;
          c.input.append(c.read_buffer, static_cast<size_t>(got));
          c.activity = now;
        } else if (c.ssl) {
          int e = SSL_get_error(c.ssl.get(), got);
          if (e == SSL_ERROR_WANT_READ)
            c.tls_wait = POLLIN;
          else if (e == SSL_ERROR_WANT_WRITE)
            c.tls_wait = POLLOUT;
          else
            drop = true;
        } else if (got == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR))
          drop = true;
      }
      if (!drop && !c.inflight && c.output.empty() && c.input.size() >= 4) {
        auto *p = reinterpret_cast<const unsigned char *>(c.input.data());
        uint32_t len = (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) |
                       uint32_t(p[3]);
        if (len == 0 || len > MAX_FRAME) {
          ++malformed;
          drop = true;
        } else if (c.input.size() >= size_t(len) + 4) {
          std::string payload = c.input.substr(4, len);
          c.input.erase(0, len + 4);
          c.frame_start = c.input.empty() ? Clock::time_point{} : now;
          c.tokens =
              std::min(100.0, c.tokens + std::chrono::duration<double>(now - c.last).count() * 50);
          c.last = now;
          Request req;
          std::string err;
          if (c.tokens < 1) {
            ++rate_limited;
            c.output = frame(error_json("RATE_LIMIT"));
          } else if (!parse_request(payload, req, err)) {
            --c.tokens;
            ++malformed;
            c.output = frame(error_json(err));
          } else {
            --c.tokens;
            if (c.ssl && req.kind == RequestKind::Command && req.command.client != c.principal) {
              c.output = frame(error_json("IDENTITY_MISMATCH"));
            } else if (c.ssl && req.kind != RequestKind::Command && !admins.count(c.principal)) {
              c.output = frame(error_json("FORBIDDEN"));
            } else if (req.kind == RequestKind::Metrics) {
              size_t depth = 0;
              for (auto &q : queues)
                depth += q->size();
              auto hist_json = [](const auto &hist) {
                std::string out = "[";
                for (std::size_t j = 0; j < hist.size(); ++j) {
                  if (j)
                    out += ",";
                  out += std::to_string(hist[j].load());
                }
                return out + "]";
              };
              std::string histories = "[";
              for (size_t i = 0; i < opt.shards; ++i) {
                if (i)
                  histories += ",";
                histories += std::to_string(history_usage[i].load());
              }
              histories += "]";
              c.output =
                  frame("{\"history_usage\":" + histories +
                        ",\"history_capacity\":" + std::to_string(opt.max_history) +
                        ",\"queue_wait_log2_ns\":" + hist_json(queue_hist) +
                        ",\"service_log2_ns\":" + hist_json(service_hist) +
                        ",\"batches\":" + std::to_string(batches.load()) +
                        ",\"wal_syncs\":" + std::to_string(wal_syncs.load()) +
                        ",\"queue_depth\":" + std::to_string(depth) +
                        ",\"queue_capacity\":" + std::to_string(opt.queue_capacity * opt.shards) +
                        ",\"rejected_commands\":" + std::to_string(rejected_commands.load()) +
                        ",\"accepted_connections\":" + std::to_string(accepted) +
                        ",\"processed\":" + std::to_string(processed.load()) + ",\"overload\":" +
                        std::to_string(overload) + ",\"malformed\":" + std::to_string(malformed) +
                        ",\"rate_limited\":" + std::to_string(rate_limited) + "}");
            } else if (req.kind == RequestKind::Command || opt.shards == 1) {
              size_t shard = req.kind == RequestKind::Command
                                 ? hash_symbol(req.command.symbol) % opt.shards
                                 : 0;
              if (queues[shard]->try_push(Work{c.id, std::move(req)}))
                c.inflight = true;
              else {
                ++overload;
                c.output = frame(error_json("OVERLOADED"));
              }
            } else {
              c.parts.assign(opt.shards, {});
              c.pending = 0;
              c.aggregate_error = false;
              for (size_t shard = 0; shard < opt.shards; ++shard) {
                if (queues[shard]->try_push(Work{c.id, req}))
                  ++c.pending;
                else {
                  c.aggregate_error = true;
                  ++overload;
                }
              }
              if (c.pending)
                c.inflight = true;
              else {
                c.parts.clear();
                c.output = frame(error_json("OVERLOADED"));
              }
            }
          }
        }
      }
      if (!drop && ((c.handshake &&
                     now - c.connected > std::chrono::milliseconds(opt.handshake_timeout_ms)) ||
                    now - c.activity > std::chrono::milliseconds(opt.idle_timeout_ms) ||
                    (!c.input.empty() &&
                     now - c.frame_start > std::chrono::milliseconds(opt.frame_timeout_ms))))
        drop = true;
      if (drop) {
        close(c.fd);
        clients.erase(it);
      }
    }
  }
  close(listener);
  for (auto &[id, c] : clients) {
    (void)id;
    close(c.fd);
  }
  for (auto &q : queues)
    q->close();
  for (auto &t : workers)
    t.join();
  if (lockfd >= 0)
    close(lockfd);
  close(wake[0]);
  close(wake[1]);
  sigaction(SIGTERM, &oldterm, nullptr);
  sigaction(SIGINT, &oldint, nullptr);
  sigaction(SIGPIPE, &oldpipe, nullptr);
  std::cout << (fatal ? "{\"event\":\"stopped\",\"status\":\"error\"}"
                      : "{\"event\":\"stopped\",\"status\":\"clean\"}")
            << std::endl;
  return fatal ? 1 : 0;
}
}
