#pragma once
#include "asterbook/book.hpp"
#include <cstddef>
#include <map>
#include <optional>
namespace asterbook {
enum class AllocatorMode { Baseline, Pooled };
struct EngineOptions {
  std::size_t max_live = 100000, max_history = 1000000;
  AllocatorMode allocator = AllocatorMode::Baseline;
};
class Engine {
public:
  explicit Engine(std::size_t max_live = 100000, std::size_t max_history = 1000000);
  explicit Engine(EngineOptions options);
  Engine(Engine &&) = default;
  Engine(const Engine &) = delete;
  Result execute(const Command &command);
  std::optional<Result> precheck(const Command &command) const;
  std::string state() const;
  bool validate() const { return book_.validate(); }
  std::uint64_t sequence() const noexcept { return sequence_; }
  std::size_t history_capacity() const noexcept { return max_history_; }
  std::string checkpoint() const;
  void restore(const std::string &data);

private:
  Book book_;
  std::size_t max_live_, max_history_;
  AllocatorMode allocator_;
  std::uint64_t sequence_ = 0;
  std::map<std::pair<std::string, std::string>, std::pair<std::string, Result>> dedup_;
};
std::string canonical_command(const Command &);
} // namespace asterbook
