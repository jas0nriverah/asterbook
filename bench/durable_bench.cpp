#include "asterbook/journal.hpp"
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <unistd.h>
using namespace asterbook;
int main(int argc, char **argv) {
  int n = argc > 1 ? std::atoi(argv[1]) : 1000;
  int batch = argc > 2 ? std::atoi(argv[2]) : 1;
  if (n < 1 || batch < 1 || batch > 1024)
    return 2;
  auto path = (std::filesystem::temp_directory_path() / ("asterbench-" + std::to_string(getpid())))
                  .string();
  Engine oracle(n + 10, n + 10);
  std::vector<Command> commands;
  for (int i = 0; i < n; ++i) {
    Command c;
    c.client = "bench";
    c.request = std::to_string(i);
    c.id = c.request;
    c.symbol = "S";
    c.price = 10;
    c.qty = 1;
    commands.push_back(c);
    oracle.execute(c);
  }
  {
    DurableEngine e(path, n + 10, n + 10);
    auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < n; i += batch) {
      auto end = std::min(n, i + batch);
      e.execute_batch(std::vector<Command>(commands.begin() + i, commands.begin() + end));
    }
    auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    if (e.state() != oracle.state())
      return 1;
    std::cout << "{\"commands\":" << n << ",\"batch_size\":" << batch
              << ",\"syncs\":" << e.sync_count() << ",\"seconds\":" << seconds
              << ",\"commands_per_second\":" << n / seconds << "}\n";
  }
  {
    DurableEngine recovered(path, n + 10, n + 10);
    if (recovered.state() != oracle.state())
      return 1;
  }
  std::filesystem::remove(path);
}
