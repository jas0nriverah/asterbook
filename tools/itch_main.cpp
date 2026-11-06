#include "asterbook/itch.hpp"
#include <fstream>
#include <iostream>
#include <vector>
#include <iomanip>
#include <stdexcept>
static void json(const std::string &s) {
  std::cout << '"';
  for (unsigned char c : s) {
    if (c == '"' || c == '\\')
      std::cout << '\\' << c;
    else if (c < 32)
      std::cout << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
    else
      std::cout << c;
  }
  std::cout << '"';
}
int main(int argc, char **argv) {
  bool sequenced = argc == 3 && std::string(argv[1]) == "--sequenced";
  if (!(argc == 2 || sequenced)) {
    std::cerr << "usage: asterbook_itch [--sequenced] FILE\n";
    return 2;
  }
  try {
    std::ifstream f(argv[sequenced ? 2 : 1], std::ios::binary);
    if (!f)
      throw std::runtime_error("cannot open file");
    asterbook::itch::Reconstructor b;
    while (true) {
      int a = f.get();
      if (a == EOF) {
        if (f.bad())
          throw std::runtime_error("read failure");
        break;
      }
      uint64_t seq = 0;
      if (sequenced) {
        seq = static_cast<unsigned char>(a);
        for (int i = 1; i < 8; ++i) {
          int c = f.get();
          if (c == EOF)
            throw std::runtime_error("truncated sequence");
          seq = (seq << 8) | static_cast<unsigned char>(c);
        }
        a = f.get();
        if (a == EOF)
          throw std::runtime_error("truncated length");
      }
      int c = f.get();
      if (c == EOF)
        throw std::runtime_error("truncated length");
      size_t n = (a << 8) | c;
      std::vector<uint8_t> p(n);
      if (!f.read(reinterpret_cast<char *>(p.data()), n))
        throw std::runtime_error("truncated payload");
      if (sequenced)
        b.apply_sequenced(seq, p);
      else
        b.apply(p);
    }
    std::cout << '[';
    bool first = true;
    for (auto &[ref, o] : b.orders()) {
      if (!first)
        std::cout << ',';
      first = false;
      std::cout << "{\"reference\":" << ref << ",\"locate\":" << o.locate << ",\"side\":";
      json(std::string(1, o.side));
      std::cout << ",\"shares\":" << o.shares << ",\"symbol\":";
      json(o.symbol);
      std::cout << ",\"price\":" << o.price << ",\"attribution\":";
      if (o.attribution)
        json(*o.attribution);
      else
        std::cout << "null";
      std::cout << '}';
    }
    std::cout << "]\n";
  } catch (const std::exception &e) {
    std::cerr << "feed error: " << e.what() << '\n';
    return 1;
  }
}
