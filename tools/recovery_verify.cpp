#include "asterbook/journal.hpp"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
int main(int argc, char **argv) {
  if (argc < 2 || argc > 3) {
    std::cerr << "usage: recovery_verify WAL [SNAPSHOT]\n";
    return 2;
  }
  try {
    std::ifstream in(argv[1], std::ios::binary);
    unsigned char h[20];
    if (!in.read(reinterpret_cast<char *>(h), sizeof h) || h[0] != 'A' || h[1] != 'B' ||
        h[2] != 'H' || h[3] != '1')
      throw std::runtime_error("invalid WAL header");
    auto get = [&](int off) {
      std::uint64_t n = 0;
      for (int i = 0; i < 8; ++i)
        n |= std::uint64_t(h[off + i]) << (8 * i);
      return n;
    };
    auto live = get(4), history = get(12);
    if (!live || !history || live > 10000 || history > 1000000)
      throw std::runtime_error("invalid capacities");
    asterbook::DurableEngine e(argv[1], live, history, argc == 3 ? argv[2] : "");
    std::cout << e.state() << '\n';
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
