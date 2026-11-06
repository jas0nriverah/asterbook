#include "asterbook/gateway.hpp"
#include "asterbook/protocol.hpp"
#include "asterbook/journal.hpp"
#include "asterbook/queue.hpp"
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
  uint64_t id;
  size_t shard;
  std::string payload;
};
struct Client {
  int fd;
  uint64_t id;
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
int run_gateway(const GatewayOptions &opt) {
  stopping = 0;
  in_addr addr{};
  if (inet_pton(AF_INET, opt.bind.c_str(), &addr) != 1) {
    std::cerr << "invalid IPv4 bind address\n";
    return 1;
  }
  if ((ntohl(addr.s_addr) >> 24) != 127 && !opt.unsafe_non_loopback) {
    std::cerr << "non-loopback bind requires --unsafe-non-loopback\n";
    return 1;
  }
  int listener = socket(AF_INET, SOCK_STREAM, 0);
  if (listener < 0) {
    perror("socket");
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
    perror("listen");
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
    perror("pipe");
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
  std::atomic<uint64_t> processed = 0;
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
      engines.push_back(std::make_unique<DurableEngine>(base + "/journal", opt.max_live,
                                                        opt.max_history,
                                                        std::filesystem::exists(snap) ? snap : ""));
      queues.push_back(std::make_unique<BoundedQueue<Work>>(opt.queue_capacity));
    }
    for (size_t i = 0; i < opt.shards; ++i)
      workers.emplace_back([&, i, engine = std::move(engines[i]), snap = snapshots[i]]() mutable {
        Work w;
        while (!fatal && queues[i]->pop(w)) {
          try {
            std::string result;
            if (w.request.kind == RequestKind::State)
              result = engine->state();
            else if (w.request.kind == RequestKind::Checkpoint) {
              engine->snapshot(snap);
              result = "{\"ok\":true}";
            } else {
              if (Clock::now() - w.admitted > std::chrono::milliseconds(opt.queue_timeout_ms))
                result = error_json("DEADLINE_EXCEEDED");
              else
                result = result_json(engine->execute(w.request.command));
              if (opt.shards > 1)
                result = "{\"shard\":" + std::to_string(i) + ",\"result\":" + result + "}";
            }
            if (result.size() > MAX_OUTPUT - 4) {
              if (w.request.kind == RequestKind::Command)
                throw std::runtime_error("command response exceeded invariant");
              result = error_json("RESPONSE_TOO_LARGE");
            }
            {
              std::lock_guard lock(cm);
              completions.push_back({w.id, i, std::move(result)});
            }
            ++processed;
            char b = 1;
            (void)write(wake[1], &b, 1);
          } catch (const std::exception &e) {
            std::cerr << "fatal engine/storage error: " << e.what() << "\n";
            fatal = true;
            char b = 1;
            (void)write(wake[1], &b, 1);
            break;
          }
        }
      });
  } catch (const std::exception &e) {
    std::cerr << "engine startup: " << e.what() << "\n";
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
  std::cout << "listening " << opt.bind << ":" << opt.port << std::endl;
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
    for (auto &[id, c] : clients) {
      short events = 0;
      if (!c.inflight && c.output.empty())
        events |= POLLIN;
      if (!c.output.empty())
        events |= POLLOUT;
      pf.push_back({c.fd, events, 0});
      ids.push_back(id);
    }
    int n = poll(pf.data(), pf.size(), 50);
    if (n < 0) {
      if (errno == EINTR)
        continue;
      perror("poll");
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
            perror("accept");
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
      if (!drop && (re & POLLOUT)) {
        ssize_t sent = send(c.fd, c.output.data() + c.offset, c.output.size() - c.offset, 0);
        if (sent > 0) {
          c.offset += static_cast<size_t>(sent);
          c.activity = now;
          if (c.offset == c.output.size()) {
            c.output.clear();
            c.offset = 0;
          }
        } else if (sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
          drop = true;
      }
      if (!drop && (re & POLLIN)) {
        char buf[4096];
        ssize_t got = recv(c.fd, buf, sizeof(buf), 0);
        if (got == 0)
          drop = true;
        else if (got < 0) {
          if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
            drop = true;
        } else {
          if (c.input.empty())
            c.frame_start = now;
          c.input.append(buf, static_cast<size_t>(got));
          c.activity = now;
        }
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
            if (req.kind == RequestKind::Metrics) {
              size_t depth = 0;
              for (auto &q : queues)
                depth += q->size();
              c.output = frame("{\"queue_depth\":" + std::to_string(depth) +
                               ",\"accepted_connections\":" + std::to_string(accepted) +
                               ",\"processed\":" + std::to_string(processed.load()) +
                               ",\"overload\":" + std::to_string(overload) +
                               ",\"malformed\":" + std::to_string(malformed) +
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
      if (!drop && (now - c.activity > std::chrono::milliseconds(opt.idle_timeout_ms) ||
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
  return fatal ? 1 : 0;
}
}
