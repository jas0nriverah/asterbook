#pragma once
#include "asterbook/engine.hpp"
#include <string>
namespace asterbook {
class DurableEngine {
public:
  explicit DurableEngine(const std::string &path, std::size_t max_live = 100000,
                         std::size_t max_history = 1000000, const std::string &snapshot_path = "");
  ~DurableEngine();
  DurableEngine(const DurableEngine &) = delete;
  DurableEngine &operator=(const DurableEngine &) = delete;
  Result execute(const Command &);
  std::string state() const { return engine_.state(); }
  void snapshot(const std::string &path) const;

private:
  int fd_ = -1;
  std::string path_;
  Engine engine_;
  bool poisoned_ = false;
};
}
