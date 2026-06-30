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

def check_range(start, end, queue):
    for num in range(start, end):
        if is_prime(num):
            queue.put(num)

def find_primes(limit, num_processes):
    manager = mp.Manager()
    queue = manager.Queue()
    pool = mp.Pool(processes=num_processes)
    
    # Estimate upper bound for the nth prime (n=limit)
    if limit < 6:
        upper_bound = 12  # Small fixed upper bound for small limits
    else:
        n = limit
        upper_bound = int(n * (math.log(n) + math.log(math.log(n)))) + 10000
    
    chunk_size = upper_bound // num_processes
    processes = []
    
    start_time = time.time()
    
    for i in range(num_processes):
        start = i * chunk_size
        end = start + chunk_size if i != num_processes - 1 else upper_bound
        p = pool.apply_async(check_range, args=(start, end, queue))
        processes.append(p)
    
    for p in processes:
        p.get()
    
    pool.close()
    pool.join()
    
    primes = []
    while not queue.empty():
        primes.append(queue.get())
    
    primes.sort()
    primes = primes[:limit]
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    return primes, execution_time

def main():
    num_processes = mp.cpu_count()
    primes, execution_time = find_primes(10000, num_processes)
    
    result = {
        "primes": primes,
        "count": len(primes),
        "execution_time": execution_time
    }
    
    with open("primes.json", "w") as f:
        json.dump(result, f, indent=2)
    
    print(f"Found {len(primes)} primes in {execution_time:.4f} seconds.")

if __name__ == "__main__":
    main()
