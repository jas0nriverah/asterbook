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
  const std::string allocator = argc > 3 ? argv[3] : "baseline";
  if ((allocator != "baseline" && allocator != "pooled") || n < 1 ||
      (workload != "add" && workload != "cancel" && workload != "cross" && workload != "deep")) {
    std::cerr << "usage: core_bench [count] [add|cancel|cross|deep] [baseline|pooled]\n";
    return 2;
  }
  EngineOptions options{static_cast<size_t>(n) * 12 + 10, static_cast<size_t>(n) * 20 + 10,
                        allocator == "pooled" ? AllocatorMode::Pooled : AllocatorMode::Baseline};
  Engine engine(options);
  options.allocator = AllocatorMode::Baseline;
  Engine oracle(options);
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
    for (int i = 0; i < (workload == "deep" ? n * 10 : n); ++i) {
      auto c = command(i, Side::Sell, workload == "deep" ? 100 + i % 1000 : 100, 1);
      engine.execute(c);
      oracle.execute(c);
    }
  std::vector<long long> ns;
  ns.reserve(n);
  size_t events = 0, rejected = 0;
  long long total_ns = 0;
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
    auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
    ns.push_back(duration);
    total_ns += duration;
    auto expected = oracle.execute(c);
    if (r.sequence != expected.sequence || r.accepted != expected.accepted ||
        r.code != expected.code || r.events.size() != expected.events.size()) {
      std::cerr << "oracle mismatch\n";
      return 1;
    }
    events += r.events.size();
    rejected += !r.accepted;
  }
  if (engine.checkpoint() != oracle.checkpoint()) {
    std::cerr << "state mismatch\n";
    return 1;
  }
  auto elapsed = total_ns / 1e9;
  std::sort(ns.begin(), ns.end());
  auto p = [&](double q) { return ns[static_cast<size_t>(q * (ns.size() - 1))]; };
  std::cout << "{\"allocator\":\"" << allocator << "\",\"workload\":\"" << workload
            << "\",\"samples\":" << n
            << ",\"setup_orders\":" << (workload == "add" ? 0 : (workload == "deep" ? n * 10 : n))
            << ",\"events\":" << events << ",\"rejected\":" << rejected
            << ",\"elapsed_seconds\":" << elapsed << ",\"commands_per_second\":" << n / elapsed
            << ",\"latency_ns\":{\"p50\":" << p(.5) << ",\"p95\":" << p(.95)
            << ",\"p99\":" << p(.99) << "}}\n";
}
