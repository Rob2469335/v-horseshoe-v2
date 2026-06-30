import multiprocessing
import time
import json
import math

def is_prime(n):
    if n <= 1:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    for i in range(3, int(math.sqrt(n)) + 1, 2):
        if n % i == 0:
            return False
    return True

def generate_primes(count):
    primes = []
    num = 2
    while len(primes) < count:
        if is_prime(num):
            primes.append(num)
        num += 1
    return primes

def worker(count, result_queue):
    primes = generate_primes(count)
    result_queue.put(primes)

def main():
    start_time = time.time()
    num_workers = multiprocessing.cpu_count()
    count_per_worker = 10000 // num_workers
    remainder = 10000 % num_workers

    processes = []
    result_queue = multiprocessing.Queue()

    for i in range(num_workers):
        worker_count = count_per_worker + (1 if i < remainder else 0)
        p = multiprocessing.Process(target=worker, args=(worker_count, result_queue))
        processes.append(p)
        p.start()

    all_primes = []
    for _ in range(num_workers):
        primes = result_queue.get()
        all_primes.extend(primes)

    for p in processes:
        p.join()

    all_primes.sort()
    first_10000_primes = all_primes[:10000]

    end_time = time.time()
    execution_time = end_time - start_time

    result_data = {
        "primes": first_10000_primes,
        "execution_time": execution_time
    }

    with open("primes.json", "w") as f:
        json.dump(result_data, f, indent=2)

    print(f"Execution time: {execution_time:.4f} seconds")
    print("First 10 primes:", first_10000_primes[:10])

if __name__ == "__main__":
    main()
