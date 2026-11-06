#include "asterbook/engine.hpp"
#include <benchmark/benchmark.h>
#include <memory>
using namespace asterbook;

static Command order(const char *request, const char *id, Side side, int price, int qty = 1) {
  Command c;
  c.client = "bench";
  c.request = request;
  c.symbol = "XYZ";
  c.id = id;
  c.side = side;
  c.price = price;
  c.qty = qty;
  return c;
}
static void core(benchmark::State &state) {
  const auto mode = state.range(0) ? AllocatorMode::Pooled : AllocatorMode::Baseline;
  const auto workload = state.range(1);
  const auto resting = order("resting", "resting", Side::Sell, 100);
  auto measured = order("measured", "measured", Side::Buy, workload == 0 ? 99 : 100);
  if (workload == 2) {
    measured.op = Op::Cancel;
    measured.id = "resting";
  }
  for (auto _ : state) {
    (void)_;
    state.PauseTiming();
    auto engine = std::make_unique<Engine>(EngineOptions{100, 100, mode});
    if (workload != 0) {
      auto result = engine->execute(resting);
      if (!result.accepted) {
        state.SkipWithError("setup rejected");
        break;
      }
    }
    state.ResumeTiming();
    {
      auto result = engine->execute(measured);
      benchmark::DoNotOptimize(result);
      benchmark::ClobberMemory();
      state.PauseTiming();
      if (!result.accepted)
        state.SkipWithError("measured command rejected");
    }
    engine.reset();
    state.ResumeTiming();
  }
  state.SetItemsProcessed(state.iterations());
}
BENCHMARK(core)->Args({0, 0})->Args({1, 0})->Args({0, 1})->Args({1, 1})->Args({0, 2})->Args({1, 2});
BENCHMARK_MAIN();
