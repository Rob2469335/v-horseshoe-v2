import multiprocessing as mp
import time
import json
import math

def is_prime(n):
    if n <= 1:
        return False
    if n <= 3:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True

def get_primes_in_range(args):
    start, end = args
    primes = []
    for num in range(start, end):
        if is_prime(num):
            primes.append(num)
    return primes

def find_primes_multiprocess(limit, num_processes):
    # Estimate upper bound for the nth prime (n=limit)
    if limit < 6:
        upper_bound = 12
    else:
        n = limit
        upper_bound = int(n * (math.log(n) + math.log(math.log(n)))) + 10000
    
    chunk_size = max(1, upper_bound // num_processes)
    ranges = []
    for i in range(num_processes):
        start = i * chunk_size
        end = start + chunk_size if i != num_processes - 1 else upper_bound
        ranges.append((start, end))
    
    start_time = time.time()
    with mp.Pool(processes=num_processes) as pool:
        results = pool.map(get_primes_in_range, ranges)
    
    primes = []
    for r in results:
        primes.extend(r)
    
    primes.sort()
    primes = primes[:limit]
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    return primes, execution_time

def find_primes_single(limit):
    if limit < 6:
        upper_bound = 12
    else:
        n = limit
        upper_bound = int(n * (math.log(n) + math.log(math.log(n)))) + 10000
        
    start_time = time.time()
    primes = get_primes_in_range((0, upper_bound))
    primes.sort()
    primes = primes[:limit]
    execution_time = time.time() - start_time
    return primes, execution_time

def main():
    target = 500000
    print(f"--- Benchmarking First {target} Primes ---")
    
    # 1. Single Thread
    print("\nRunning Single-Threaded...")
    single_primes, single_time = find_primes_single(target)
    print(f"Single-Threaded Time: {single_time:.4f} seconds")
    
    # 2. Multi-Process
    num_processes = mp.cpu_count()
    print(f"\nRunning Multi-Processed ({num_processes} cores)...")
    multi_primes, multi_time = find_primes_multiprocess(target, num_processes)
    print(f"Multi-Processed Time: {multi_time:.4f} seconds")
    
    # Validation
    assert single_primes == multi_primes, "Mismatch in calculated primes!"
    
    print(f"\nSpeedup: {single_time / multi_time:.2f}x")
    print("Verification Passed! Primes match.")
    print(f"Last Prime Found (10,000th): {multi_primes[-1]}")
    
    result = {
        "target": target,
        "primes": multi_primes,
        "single_thread_time": single_time,
        "multi_process_time": multi_time,
        "speedup": single_time / multi_time
    }
    
    with open("primes_benchmark.json", "w") as f:
        json.dump(result, f, indent=2)

if __name__ == "__main__":
    main()
