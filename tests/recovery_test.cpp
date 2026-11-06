#include "asterbook/journal.hpp"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <unistd.h>
using namespace asterbook;
int main() {
  auto dir =
      std::filesystem::temp_directory_path() / ("asterbook-test-" + std::to_string(getpid()));
  std::filesystem::create_directory(dir);
  auto wal = (dir / "wal").string(), snap = (dir / "snap").string();
  Command c;
  c.client = "a";
  c.request = "1";
  c.symbol = "X";
  c.id = "1";
  c.price = 10;
  c.qty = 3;
  std::string state;
  {
    DurableEngine e(wal);
    auto r = e.execute(c);
    assert(r.accepted);
    e.snapshot(snap);
    c.request = "2";
    c.id = "2";
    assert(e.execute(c).accepted);
    state = e.state();
    c.request = "1";
    c.id = "1";
    bool locked = false;
    try {
      DurableEngine second(wal);
    } catch (...) {
      locked = true;
    }
    assert(locked);
  }
  {
    DurableEngine e(wal, 100000, 1000000, snap);
    assert(e.state() == state);
    assert(e.execute(c).duplicate);
    c.qty = 4;
    assert(e.execute(c).code == "REQUEST_CONFLICT");
  }
  {
    auto empty = (dir / "empty").string();
    std::ofstream out(empty);
    out.close();
    bool bad = false;
    try {
      DurableEngine e(empty);
    } catch (...) {
      bad = true;
    }
    assert(bad);
  }
  {
    auto missing = (dir / "missing").string();
    bool bad = false;
    try {
      DurableEngine e(missing, 100000, 1000000, snap);
    } catch (...) {
      bad = true;
    }
    assert(bad);
    assert(!std::filesystem::exists(missing));
  }
  {
    auto batch_wal = (dir / "batch").string();
    Command first = c;
    first.request = "batch1";
    first.id = "batch1";
    first.qty = 3;
    Command conflict = first;
    conflict.qty = 4;
    Command second = first;
    second.request = "batch2";
    second.id = "batch2";
    std::string expected;
    {
      DurableEngine e(batch_wal);
      auto results = e.execute_batch({first, first, conflict, second});
      assert(results.size() == 4 && results[0].accepted && results[1].duplicate);
      assert(results[2].code == "REQUEST_CONFLICT" && results[3].sequence == 2);
      assert(e.sync_count() == 1);
      assert(e.execute_batch({}).empty());
      assert(e.execute_batch({first, conflict})[0].duplicate && e.sync_count() == 1);
      expected = e.state();
    }
    DurableEngine reopened(batch_wal);
    assert(reopened.state() == expected);
    assert(reopened.execute(first).duplicate);
  }
  {
    auto batch_wal = (dir / "capacity-batch").string();
    Command a = c;
    a.request = "a";
    a.id = "a";
    a.qty = 1;
    Command b = a;
    b.request = "b";
    b.id = "b";
    b.price = 0;
    Command d = a;
    d.request = "d";
    d.id = "d";
    Command conflict = a;
    conflict.qty = 9;
    std::string expected;
    {
      DurableEngine e(batch_wal, 100, 2);
      assert(e.execute(a).accepted);
      auto results = e.execute_batch({a, conflict, b, b, d});
      assert(results[0].duplicate && results[1].code == "REQUEST_CONFLICT");
      assert(results[2].code == "INVALID_PRICE_OR_QTY" && results[2].sequence == 2);
      assert(results[3].duplicate && results[4].code == "HISTORY_CAPACITY");
      expected = e.state();
    }
    DurableEngine reopened(batch_wal, 100, 2);
    assert(reopened.state() == expected);
    assert(reopened.execute(b).duplicate);
    assert(reopened.execute(d).code == "HISTORY_CAPACITY");
  }
  // Equal-sized, independently valid records cannot be reordered.
  {
    auto reordered = (dir / "reordered").string();
    std::ifstream in(wal, std::ios::binary);
    std::string bytes((std::istreambuf_iterator<char>(in)), {});
    auto u32 = [&](std::size_t p) {
      return std::uint32_t((unsigned char)bytes[p]) |
             (std::uint32_t((unsigned char)bytes[p + 1]) << 8) |
             (std::uint32_t((unsigned char)bytes[p + 2]) << 16) |
             (std::uint32_t((unsigned char)bytes[p + 3]) << 24);
    };
    auto n1 = 12 + u32(24);
    auto n2 = 12 + u32(20 + n1 + 4);
    assert(n1 == n2);
    std::string swapped = bytes.substr(0, 20) + bytes.substr(20 + n1, n2) + bytes.substr(20, n1) +
                          bytes.substr(20 + n1 + n2);
    std::ofstream out(reordered, std::ios::binary);
    out.write(swapped.data(), swapped.size());
    out.close();
    bool bad = false;
    try {
      DurableEngine e(reordered);
    } catch (...) {
      bad = true;
    }
    assert(bad);
  }
  {
    auto corrupt = (dir / "header").string();
    std::filesystem::copy_file(wal, corrupt);
    std::fstream f(corrupt, std::ios::in | std::ios::out | std::ios::binary);
    f.seekp(0);
    f.put('X');
    f.close();
    bool bad = false;
    try {
      DurableEngine e(corrupt);
    } catch (...) {
      bad = true;
    }
    assert(bad);
  }
  // Complete record corruption and checkpoint corruption must fail closed.
  {
    auto corrupt = (dir / "corrupt").string();
    std::filesystem::copy_file(wal, corrupt);
    std::fstream f(corrupt, std::ios::in | std::ios::out | std::ios::binary);
    f.seekg(-1, std::ios::end);
    char x;
    f.get(x);
    x ^= 1;
    f.seekp(-1, std::ios::end);
    f.put(x);
    f.close();
    bool bad = false;
    try {
      DurableEngine e(corrupt);
    } catch (...) {
      bad = true;
    }
    assert(bad);
  }
  {
    auto corrupt = (dir / "bad-snap").string();
    std::filesystem::copy_file(snap, corrupt);
    std::fstream f(corrupt, std::ios::in | std::ios::out | std::ios::binary);
    f.seekg(-1, std::ios::end);
    char x;
    f.get(x);
    x ^= 1;
    f.seekp(-1, std::ios::end);
    f.put(x);
    f.close();
    bool bad = false;
    try {
      DurableEngine e(wal, 100000, 1000000, corrupt);
    } catch (...) {
      bad = true;
    }
    assert(bad);
  }
  bool config_bad = false;
  try {
    DurableEngine e(wal, 2, 1000000);
  } catch (...) {
    config_bad = true;
  }
  assert(config_bad);
  {
    std::ofstream f(wal, std::ios::app | std::ios::binary);
    f << 'x';
  }
  bool bad = false;
  try {
    DurableEngine e(wal);
  } catch (...) {
    bad = true;
  }
  assert(bad);
  std::filesystem::remove_all(dir);
}
