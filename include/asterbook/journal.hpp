#pragma once
#include "asterbook/engine.hpp"
#include <string>
#include <vector>
namespace asterbook {
class DurableEngine {
public:
  explicit DurableEngine(const std::string &path, std::size_t max_live = 100000,
                         std::size_t max_history = 1000000, const std::string &snapshot_path = "",
                         AllocatorMode allocator = AllocatorMode::Baseline);
  ~DurableEngine();
  DurableEngine(const DurableEngine &) = delete;
  DurableEngine &operator=(const DurableEngine &) = delete;
  Result execute(const Command &);
  std::vector<Result> execute_batch(const std::vector<Command> &);
  std::string state() const { return engine_.state(); }
  void snapshot(const std::string &path) const;
  std::uint64_t sync_count() const noexcept { return sync_count_; }

private:
  int fd_ = -1;
  std::string path_;
  Engine engine_;
  bool poisoned_ = false;
  std::uint64_t sync_count_ = 0;
};
} // namespace asterbook
