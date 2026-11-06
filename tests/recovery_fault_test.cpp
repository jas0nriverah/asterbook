#include "asterbook/journal.hpp"
#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <sys/wait.h>
#include <unistd.h>
using namespace asterbook;
Command command() {
  Command c;
  c.client = "a";
  c.request = "r";
  c.symbol = "X";
  c.id = "id";
  c.price = 1;
  c.qty = 1;
  return c;
}
int main() {
  auto dir =
      std::filesystem::temp_directory_path() / ("asterbook-fault-" + std::to_string(getpid()));
  std::filesystem::create_directory(dir);
  for (const char *stage : {"after_append", "after_sync", "after_apply"}) {
    auto path = (dir / stage).string();
    {
      DurableEngine init(path);
    }
    auto pid = fork();
    assert(pid >= 0);
    if (pid == 0) {
      setenv("ASTERBOOK_TEST_FAULT", stage, 1);
      DurableEngine e(path);
      auto second = command();
      second.request = "r2";
      second.id = "id2";
      e.execute_batch({command(), second});
      _exit(91);
    }
    int status = 0;
    assert(waitpid(pid, &status, 0) == pid && WIFEXITED(status) && WEXITSTATUS(status) == 90);
    DurableEngine e(path);
    auto result = e.execute(command());
    assert(result.accepted);
    auto second = command();
    second.request = "r2";
    second.id = "id2";
    auto result2 = e.execute(second);
    assert(result2.accepted);
    if (std::string(stage) != "after_append")
      assert(result.duplicate && result2.duplicate);
  }
  for (const char *stage : {"throw_append", "throw_sync"}) {
    auto path = (dir / stage).string();
    DurableEngine e(path);
    setenv("ASTERBOOK_TEST_FAULT", stage, 1);
    bool failed = false;
    try {
      e.execute(command());
    } catch (...) {
      failed = true;
    }
    unsetenv("ASTERBOOK_TEST_FAULT");
    assert(failed);
    failed = false;
    try {
      e.execute(command());
    } catch (...) {
      failed = true;
    }
    assert(failed);
  }
  std::filesystem::remove_all(dir);
}
