#include "asterbook/queue.hpp"
#include <cassert>
int main() {
  asterbook::BoundedQueue<int> q(1);
  assert(q.try_push(1));
  assert(!q.try_push(2));
  int n = 0;
  assert(q.pop(n) && n == 1);
  assert(q.try_push(3));
  q.close();
  assert(!q.try_push(4));
  assert(q.pop(n) && n == 3);
  assert(!q.pop(n));
}
