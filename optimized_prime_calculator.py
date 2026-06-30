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

def find_primes(start, end, result_queue):
    primes = []
    for num in range(start, end):
        if is_prime(num):
            primes.append(num)
    result_queue.put(primes)

def main():
    start_time = time.time()
    
    # Number of primes to find
    target_count = 10000
    
    # Use all available CPU cores
    num_processes = mp.cpu_count()
    
    # Create a manager queue to collect results
    manager = mp.Manager()
    result_queue = manager.Queue()
    
    # Create process pool
    pool = mp.Pool(processes=num_processes)
    
    # Calculate range for each process
    # We'll use a reasonable upper bound estimation for 10,000 primes
    # Prime number theorem: nth prime ~ n * ln(n)
    # For n=10000, this is approximately 10000 * ln(10000) ≈ 92000
    # We'll use 120000 to be safe
    upper_bound = 120000
    chunk_size = upper_bound // num_processes
    
    # Start processes
    processes = []
    for i in range(num_processes):
        start = i * chunk_size
        end = start + chunk_size if i < num_processes - 1 else upper_bound
        p = pool.apply_async(find_primes, args=(start, end, result_queue))
        processes.append(p)
    
    # Close pool and wait for completion
    pool.close()
    pool.join()
    
    # Collect all results
    all_primes = []
    while not result_queue.empty():
        all_primes.extend(result_queue.get())
    
    # Sort and take first 10,000 primes
    all_primes.sort()
    first_10000_primes = all_primes[:target_count]
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    # Save to JSON file
    result_data = {
        "primes": first_10000_primes,
        "execution_time_seconds": execution_time,
        "count": len(first_10000_primes)
    }
    
    with open("primes.json", "w") as f:
        json.dump(result_data, f, indent=2)
    
    print(f"Found {len(first_10000_primes)} prime numbers in {execution_time:.4f} seconds")
    print(f"Results saved to primes.json")
    
    # Print first 20 primes as verification
    print(f"First 20 primes: {first_10000_primes[:20]}")

if __name__ == "__main__":
    main()
