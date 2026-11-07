#include "asterbook/journal.hpp"
#include <chrono>
#include <cstdlib>
#include <charconv>
#include <string_view>
#include <vector>
#include <filesystem>
#include <iostream>
#include <unistd.h>
using namespace asterbook;
int main(int argc, char **argv) {
  int n = 1000, batch = 1;
  auto parse = [](const char *value, int &out) {
    std::string_view text(value);
    auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), out);
    return error == std::errc{} && end == text.data() + text.size();
  };
  if (argc > 3 || (argc > 1 && !parse(argv[1], n)) || (argc > 2 && !parse(argv[2], batch)) ||
      n < 1 || n > 1000000 || batch < 1 || batch > 1024) {
    std::cerr << "usage: durable_bench [count:1..1000000] [batch:1..1024]\n";
    return 2;
  }
  auto pattern = (std::filesystem::temp_directory_path() / "asterbench-XXXXXX").string();
  std::vector<char> temp(pattern.begin(), pattern.end());
  temp.push_back(0);
  if (!mkdtemp(temp.data()))
    return 1;
  struct Cleanup {
    std::filesystem::path directory;
    ~Cleanup() {
      std::error_code error;
      std::filesystem::remove_all(directory, error);
    }
  } cleanup{temp.data()};
  auto path = (cleanup.directory / "journal").string();
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
}
