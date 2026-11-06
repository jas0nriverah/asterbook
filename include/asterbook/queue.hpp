#pragma once
#include <condition_variable>
#include <deque>
#include <mutex>
#include <utility>
namespace asterbook {
template <class T> class BoundedQueue {
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<T> items_;
  size_t capacity_;
  bool closed_ = false;

public:
  explicit BoundedQueue(size_t n) : capacity_(n) {}
  bool try_push(T value) {
    std::lock_guard lock(mutex_);
    if (closed_ || items_.size() >= capacity_)
      return false;
    items_.push_back(std::move(value));
    cv_.notify_one();
    return true;
  }
  bool pop(T &value) {
    std::unique_lock lock(mutex_);
    cv_.wait(lock, [&] { return closed_ || !items_.empty(); });
    if (items_.empty())
      return false;
    value = std::move(items_.front());
    items_.pop_front();
    return true;
  }
  size_t size() {
    std::lock_guard lock(mutex_);
    return items_.size();
  }
  void close() {
    std::lock_guard lock(mutex_);
    closed_ = true;
    cv_.notify_all();
  }
};
}
