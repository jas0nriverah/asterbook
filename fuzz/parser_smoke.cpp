#include "asterbook/protocol.hpp"
#include <array>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <string>

int main(int argc, char **argv) {
  const unsigned long count = argc > 1 ? std::strtoul(argv[1], nullptr, 10) : 100000;
  std::mt19937_64 random(0xA57E2B00ULL);
  const std::array<std::string, 5> seeds = {
      "V1 NEW alice r1 ABC a BUY GTC 100 10", "V1 CANCEL alice r2 ABC a",
      "V1 REPLACE alice r3 ABC a b 101 2", "V1 STATE", "V1 CHECKPOINT"};
  for (unsigned long i = 0; i < count; ++i) {
    std::string data;
    if (i % 2 == 0) {
      data = seeds[random() % seeds.size()];
      const auto changes = 1 + random() % 8;
      for (unsigned long j = 0; j < changes; ++j) {
        const auto at = random() % (data.size() + 1);
        if (at == data.size())
          data.push_back(static_cast<char>(random() & 255));
        else
          data[at] = static_cast<char>(random() & 255);
      }
    } else {
      data.resize(random() % 256);
      for (char &byte : data)
        byte = static_cast<char>(random() & 255);
    }
    asterbook::Request request;
    std::string error;
    (void)asterbook::parse_request(data, request, error);
  }
  std::cout << "parser randomized smoke: " << count << " inputs, seed 0xA57E2B00\n";
}
