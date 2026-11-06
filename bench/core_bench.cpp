#include "asterbook/engine.hpp"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>
using namespace asterbook;
using Clock = std::chrono::steady_clock;
int main(int argc, char **argv) {
  const int n = argc > 1 ? std::atoi(argv[1]) : 10000;
  const std::string workload = argc > 2 ? argv[2] : "add";
  if (n < 1 ||
      (workload != "add" && workload != "cancel" && workload != "cross" && workload != "deep")) {
    std::cerr << "usage: core_bench [count] [add|cancel|cross|deep]\n";
    return 2;
  }
  Engine engine(static_cast<size_t>(n) * 12 + 10, static_cast<size_t>(n) * 20 + 10);
  auto command = [](int i, Side side, long price, long qty) {
    Command c;
    c.client = "bench";
    c.request = "r" + std::to_string(i);
    c.symbol = "S";
    c.id = "o" + std::to_string(i);
    c.side = side;
    c.price = price;
    c.qty = qty;
    return c;
  };
  // Setup excluded: preload a stable number of orders for cancel/cross/deep.
  if (workload != "add")
    for (int i = 0; i < (workload == "deep" ? n * 10 : n); ++i)
      engine.execute(command(i, Side::Sell, workload == "deep" ? 100 + i % 1000 : 100, 1));
  std::vector<long long> ns;
  ns.reserve(n);
  size_t events = 0, rejected = 0;
  const auto all_start = Clock::now();
  for (int i = 0; i < n; ++i) {
    Command c;
    if (workload == "cancel") {
      c = command(n + i, Side::Buy, 1, 1);
      c.op = Op::Cancel;
      c.id = "o" + std::to_string(i);
    } else
      c = command((workload == "deep" ? n * 10 : n) + i, Side::Buy, workload == "add" ? 99 : 1100,
                  workload == "deep" ? 10 : 1);
    auto start = Clock::now();
    auto r = engine.execute(c);
    auto end = Clock::now();
    ns.push_back(std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count());
    events += r.events.size();
    rejected += !r.accepted;
  }
  auto elapsed = std::chrono::duration<double>(Clock::now() - all_start).count();
  std::sort(ns.begin(), ns.end());
  auto p = [&](double q) { return ns[static_cast<size_t>(q * (ns.size() - 1))]; };
  std::cout << "{\"workload\":\"" << workload << "\",\"samples\":" << n
            << ",\"setup_orders\":" << (workload == "add" ? 0 : (workload == "deep" ? n * 10 : n))
            << ",\"events\":" << events << ",\"rejected\":" << rejected
            << ",\"elapsed_seconds\":" << elapsed << ",\"commands_per_second\":" << n / elapsed
            << ",\"latency_ns\":{\"p50\":" << p(.5) << ",\"p95\":" << p(.95)
            << ",\"p99\":" << p(.99) << "}}\n";
}
