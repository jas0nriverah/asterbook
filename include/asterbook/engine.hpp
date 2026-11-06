#pragma once
#include "asterbook/book.hpp"
#include <cstddef>
#include <map>
#include <optional>
namespace asterbook {
class Engine {
public:
  explicit Engine(std::size_t max_live = 100000, std::size_t max_history = 1000000);
  Result execute(const Command &command);
  std::optional<Result> precheck(const Command &command) const;
  std::string state() const;
  std::uint64_t sequence() const noexcept { return sequence_; }
  std::string checkpoint() const;
  void restore(const std::string &data);

private:
  Book book_;
  std::size_t max_live_, max_history_;
  std::uint64_t sequence_ = 0;
  std::map<std::pair<std::string, std::string>, std::pair<std::string, Result>> dedup_;
};
std::string canonical_command(const Command &);
}
