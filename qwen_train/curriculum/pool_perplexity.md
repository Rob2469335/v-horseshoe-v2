### late_binding_lambda_list
family: DATA_FLOW
difficulty: 4
why_hard: Late-binding closures make all callbacks behave identically unless you understand default-argument capture.
```python
def make_incrementers(nums):
    funcs = []
    for n in nums:
        funcs.append(lambda x: x + n)
    return funcs
```
```python
def make_incrementers(nums):
    funcs = []
    for n in nums:
        funcs.append(lambda x, n=n: x + n)
    return funcs
```
```python
from module import make_incrementers

def test():
    incs = make_incrementers([1, 2, 3])
    assert [f(10) for f in incs] == [11, 12, 13]
    incs2 = make_incrementers([-1, 0, 5])
    assert [f(0) for f in incs2] == [-1, 0, 5]

test()
print("OK")
```
### iterator_reuse_two_passes
family: DATA_FLOW
difficulty: 4
why_hard: Reusing a single iterator for multiple passes silently drops data unless you understand exhaustion.
```python
def ratio_max_min(it):
    mx = max(it)
    mn = min(it)
    return mx / mn
```
```python
def ratio_max_min(it):
    data = list(it)
    mx = max(data)
    mn = min(data)
    return mx / mn
```
```python
from module import ratio_max_min

def test():
    assert ratio_max_min(iter([2, 4, 8])) == 8 / 2
    assert ratio_max_min(iter([1, 3, 9, 9])) == 9 / 1

test()
print("OK")
```
### in_place_sort_returns_none
family: CALL_API
difficulty: 3
why_hard: list.sort mutates in place and returns None, so returning its result silently breaks callers.
```python
def sorted_desc(nums):
    return nums.sort(reverse=True)
```
```python
def sorted_desc(nums):
    nums = list(nums)
    nums.sort(reverse=True)
    return nums
```
```python
from module import sorted_desc

def test():
    assert sorted_desc([3, 1, 2]) == [3, 2, 1]
    assert sorted_desc([]) == []
    assert sorted_desc([5, 5, 1]) == [5, 5, 1]

test()
print("OK")
```
### map_object_instead_of_list
family: CALL_API
difficulty: 3
why_hard: Returning a lazy map instead of a list breaks callers expecting concrete, indexable sequences.
```python
def squares(nums):
    return map(lambda x: x * x, nums)
```
```python
def squares(nums):
    return [x * x for x in nums]
```
```python
from module import squares

def test():
    out1 = squares([1, 2, 3])
    assert out1 == [1, 4, 9]
    out2 = squares(range(5))
    assert out2 == [0, 1, 4, 9, 16]

test()
print("OK")
```
### filter_object_instead_of_list
family: CALL_API
difficulty: 3
why_hard: filter produces a one-shot iterator that surprises consumers expecting lists.
```python
def non_empty(strings):
    return filter(None, strings)
```
```python
def non_empty(strings):
    return [s for s in strings if s]
```
```python
from module import non_empty

def test():
    assert non_empty(["a", "", "b"]) == ["a", "b"]
    assert non_empty(["", " ", "x"]) == [" ", "x"]

test()
print("OK")
```
### zip_truncation_mismatched_lengths
family: COLLECTION
difficulty: 3
why_hard: zip silently truncates to the shortest input.
```python
def pairwise_sum(a, b):
    return [x + y for x, y in zip(a, b)]
```
```python
def pairwise_sum(a, b):
    if len(a) != len(b):
        raise ValueError("Lengths must match")
    return [x + y for x, y in zip(a, b)]
```
```python
from module import pairwise_sum

def test():
    assert pairwise_sum([1, 2], [3, 4]) == [4, 6]
    try:
        pairwise_sum([1, 2, 3], [10, 20])
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError on length mismatch")

test()
print("OK")
```
### mutate_list_while_iterating_skip_items
family: STATE_MUTATION
difficulty: 4
why_hard: Removing items from a list during iteration causes some elements to be skipped.
```python
def remove_even(nums):
    for i, n in enumerate(nums):
        if n % 2 == 0:
            del nums[i]
    return nums
```
```python
def remove_even(nums):
    return [n for n in nums if n % 2 != 0]
```
```python
from module import remove_even

def test():
    assert remove_even([1, 2, 3, 4]) == [1, 3]
    assert remove_even([2, 2, 2, 3]) == [3]

test()
print("OK")
```
### mutate_dict_while_iterating_keys
family: STATE_MUTATION
difficulty: 4
why_hard: Deleting keys while iterating a dict can raise errors or skip keys.
```python
def drop_small_values(d, threshold):
    for k in d:
        if d[k] < threshold:
            del d[k]
    return d
```
```python
def drop_small_values(d, threshold):
    return {k: v for k, v in d.items() if v >= threshold}
```
```python
from module import drop_small_values

def test():
    assert drop_small_values({"a": 1, "b": 5, "c": 3}, 3) == {"b": 5, "c": 3}
    assert drop_small_values({"x": 10, "y": 0}, 1) == {"x": 10}

test()
print("OK")
```
### lost_duplicates_via_set
family: COLLECTION
difficulty: 3
why_hard: Converting to a set to simplify logic silently discards duplicates.
```python
def count_unique_with_duplicates(nums):
    return len(set(nums))
```
```python
def count_unique_with_duplicates(nums):
    from collections import Counter
    c = Counter(nums)
    return sum(1 for v in c.values() if v > 1)
```
```python
from module import count_unique_with_duplicates

def test():
    assert count_unique_with_duplicates([1, 1, 2, 3]) == 1
    assert count_unique_with_duplicates([2, 2, 2, 3, 3]) == 2

test()
print("OK")
```
### groupby_missing_last_flush
family: CONTROL_FLOW
difficulty: 4
why_hard: Forgetting to flush the last group yields incomplete results.
```python
def group_runs(xs):
    groups = []
    current = []
    for x in xs:
        if not current or x == current[-1]:
            current.append(x)
        else:
            groups.append(current)
            current = [x]
    return groups
```
```python
def group_runs(xs):
    groups = []
    current = []
    for x in xs:
        if not current or x == current[-1]:
            current.append(x)
        else:
            groups.append(current)
            current = [x]
    if current:
        groups.append(current)
    return groups
```
```python
from module import group_runs

def test():
    assert group_runs([1, 1, 2, 2, 2]) == [[1, 1], [2, 2, 2]]
    assert group_runs([]) == []
    assert group_runs([1, 2, 2, 3]) == [[1], [2, 2], [3]]

test()
print("OK")
```
### shared_state_counter_leak
family: STATE_MUTATION
difficulty: 3
why_hard: A hidden global counter makes later calls depend on earlier usage.
```python
_call_count = 0

def bounded_sum(nums, limit):
    global _call_count
    _call_count += 1
    total = sum(nums)
    if _call_count > 1:
        limit = limit // 2
    return min(total, limit)
```
```python
def bounded_sum(nums, limit):
    total = sum(nums)
    return min(total, limit)
```
```python
from module import bounded_sum

def test():
    assert bounded_sum([1, 2, 3], 10) == 6
    assert bounded_sum([5, 5], 10) == 10

test()
print("OK")
```
### cache_key_uses_length_only
family: CONTRACT
difficulty: 4
why_hard: A cache keyed only by length returns the wrong result when inputs share a length.
```python
_cache = {}

def slow_concat(parts):
    key = len(parts)
    if key in _cache:
        return _cache[key]
    result = "".join(parts)
    _cache[key] = result
    return result
```
```python
_cache = {}

def slow_concat(parts):
    key = tuple(parts)
    if key in _cache:
        return _cache[key]
    result = "".join(parts)
    _cache[key] = result
    return result
```
```python
from module import slow_concat

def test():
    assert slow_concat(["a", "b"]) == "ab"
    assert slow_concat(["x", "y"]) == "xy"
    assert slow_concat(["x", "y"]) == "xy"

test()
print("OK")
```
### pure_function_mutates_argument
family: CONTRACT
difficulty: 3
why_hard: Mutating an input list when callers expect a pure function.
```python
def top_three_desc(nums):
    nums.sort(reverse=True)
    return nums[:3]
```
```python
def top_three_desc(nums):
    local = sorted(nums, reverse=True)
    return local[:3]
```
```python
from module import top_three_desc

def test():
    data = [5, 1, 3, 2]
    assert top_three_desc(data) == [5, 3, 2]
    assert data == [5, 1, 3, 2]

test()
print("OK")
```
### class_attribute_aliasing_list
family: STATE_MUTATION
difficulty: 4
why_hard: A mutable class attribute makes instances share state.
```python
class Bag:
    items = []

    def add(self, x):
        self.items.append(x)

    def snapshot(self):
        return list(self.items)
```
```python
class Bag:
    def __init__(self):
        self._items = []

    def add(self, x):
        self._items.append(x)

    def snapshot(self):
        return list(self._items)
```
```python
from module import Bag

def test():
    a = Bag()
    b = Bag()
    a.add("x")
    b.add("y")
    assert a.snapshot() == ["x"]
    assert b.snapshot() == ["y"]

test()
print("OK")
```
### aliasing_shared_dict_between_functions
family: DATA_FLOW
difficulty: 3
why_hard: Passing the same dict into helpers that mutate it changes earlier views.
```python
def add_default(settings):
    if "timeout" not in settings:
        settings["timeout"] = 10
    return settings

def mask_sensitive(settings):
    masked = {}
    for k, v in settings.items():
        masked[k] = "***" if "key" in k else v
    return masked
```
```python
def add_default(settings):
    copy = dict(settings)
    if "timeout" not in copy:
        copy["timeout"] = 10
    return copy

def mask_sensitive(settings):
    masked = {}
    for k, v in settings.items():
        masked[k] = "***" if "key" in k else v
    return masked
```
```python
from module import add_default, mask_sensitive

def test():
    cfg = {"api_key": "secret"}
    cfg2 = add_default(cfg)
    masked = mask_sensitive(cfg2)
    assert cfg == {"api_key": "secret"}
    assert masked["timeout"] == 10
    assert masked["api_key"] == "***"

test()
print("OK")
```
### any_exhausts_generator_before_second_use
family: DATA_FLOW
difficulty: 4
why_hard: any() consumes a generator, so later use sees an empty sequence.
```python
def first_even_or_all(nums):
    gen = (n for n in nums)
    has_even = any(n % 2 == 0 for n in gen)
    if has_even:
        for n in gen:
            if n % 2 == 0:
                return n
        return None
    else:
        return sum(nums)
```
```python
def first_even_or_all(nums):
    gen = list(nums)
    has_even = any(n % 2 == 0 for n in gen)
    if has_even:
        for n in gen:
            if n % 2 == 0:
                return n
        return None
    else:
        return sum(gen)
```
```python
from module import first_even_or_all

def test():
    assert first_even_or_all([1, 3, 4, 6]) == 4
    assert first_even_or_all([1, 3, 5]) == 9

test()
print("OK")
```
### reversed_iterator_exhaustion
family: CALL_API
difficulty: 3
why_hard: reversed() returns an iterator; using it twice silently loses elements.
```python
def symmetric_pairs(xs):
    rev = reversed(xs)
    forwards = list(xs)
    backwards = list(rev)
    return [(f, b) for f, b in zip(forwards, rev)]
```
```python
def symmetric_pairs(xs):
    forwards = list(xs)
    backwards = list(reversed(xs))
    return list(zip(forwards, backwards))
```
```python
from module import symmetric_pairs

def test():
    assert symmetric_pairs([1, 2, 3]) == [(1, 3), (2, 2), (3, 1)]
    assert symmetric_pairs([]) == []

test()
print("OK")
```
### manual_next_stopiteration_unhandled
family: BOUNDARY
difficulty: 3
why_hard: Forgetting to handle StopIteration crashes only at boundaries.
```python
def take_while_positive(nums):
    it = iter(nums)
    result = []
    while True:
        n = next(it)
        if n > 0:
            result.append(n)
        else:
            break
    return result
```
```python
def take_while_positive(nums):
    it = iter(nums)
    result = []
    for n in it:
        if n > 0:
            result.append(n)
        else:
            break
    return result
```
```python
from module import take_while_positive

def test():
    assert take_while_positive([1, 2, -1, 5]) == [1, 2]
    assert take_while_positive([]) == []

test()
print("OK")
```
### premature_break_outer_loop
family: CONTROL_FLOW
difficulty: 3
why_hard: A misplaced break exits early in subtle ways.
```python
def find_two_negatives(nums):
    found = []
    for n in nums:
        if n < 0:
            found.append(n)
        if len(found) == 2:
            break
    return found
```
```python
def find_two_negatives(nums):
    found = []
    for n in nums:
        if n < 0:
            found.append(n)
            if len(found) == 2:
                break
    return found
```
```python
from module import find_two_negatives

def test():
    assert find_two_negatives([1, -1, 2, -3, -5]) == [-1, -3]
    assert find_two_negatives([0, 1, -2]) == [-2]

test()
print("OK")
```
### nested_iterator_shared_object
family: DATA_FLOW
difficulty: 4
why_hard: Sharing a single iterator between nested loops produces few combinations.
```python
def cartesian_pairs(xs, ys):
    it = iter(ys)
    pairs = []
    for x in xs:
        for y in it:
            pairs.append((x, y))
    return pairs
```
```python
def cartesian_pairs(xs, ys):
    pairs = []
    for x in xs:
        for y in ys:
            pairs.append((x, y))
    return pairs
```
```python
from module import cartesian_pairs

def test():
    assert cartesian_pairs([1, 2], [3, 4]) == [(1, 3), (1, 4), (2, 3), (2, 4)]
    assert cartesian_pairs([], [1, 2]) == []

test()
print("OK")
```
### dict_last_value_wins_unexpected
family: COLLECTION
difficulty: 3
why_hard: Building a dict from pairs silently drops earlier values on repeated keys.
```python
def most_recent_counts(pairs):
    return {k: v for k, v in pairs}
```
```python
def most_recent_counts(pairs):
    result = {}
    for k, v in pairs:
        result[k] = v
    return result
```
```python
from module import most_recent_counts

def test():
    pairs = [("a", 1), ("b", 2), ("a", 3)]
    out = most_recent_counts(pairs)
    assert out["a"] == 3
    assert out["b"] == 2

test()
print("OK")
```
### sorted_keys_instead_of_values
family: EXPRESSION_VALUE
difficulty: 3
why_hard: Sorting a dict without a key function returns keys, so finding max value is subtly wrong.
```python
def max_value_key(d):
    return sorted(d)[-1]
```
```python
def max_value_key(d):
    return max(d.items(), key=lambda kv: kv[1])[0]
```
```python
from module import max_value_key

def test():
    d = {"a": 1, "z": 0, "m": 10}
    assert max_value_key(d) == "m"
    d2 = {"x": -1, "y": -2}
    assert max_value_key(d2) == "x"

test()
print("OK")
```
### list_index_vs_not_found
family: BOUNDARY
difficulty: 3
why_hard: Assuming list.index returns -1 instead of raising ValueError only fails on specific inputs.
```python
def safe_index(xs, value):
    try:
        return xs.index(value)
    except Exception:
        return -1
```
```python
def safe_index(xs, value):
    try:
        return xs.index(value)
    except ValueError:
        return -1
```
```python
from module import safe_index

def test():
    assert safe_index([1, 2, 3], 2) == 1
    assert safe_index([1, 2, 3], 4) == -1
    try:
        safe_index(None, 1)
    except TypeError:
        pass
    else:
        raise AssertionError("Expected TypeError for None")

test()
print("OK")
```
### generator_materialization_missing
family: DATA_FLOW
difficulty: 3
why_hard: Returning a generator when callers expect a reusable sequence.
```python
def positives(nums):
    return (n for n in nums if n > 0)
```
```python
def positives(nums):
    return [n for n in nums if n > 0]
```
```python
from module import positives

def test():
    gen = positives([-1, 0, 1, 2])
    assert gen == [1, 2]
    assert positives([]) == []

test()
print("OK")
```
### stateful_iterator_object
family: STATE_MUTATION
difficulty: 4
why_hard: Making an object its own iterator causes second iteration to yield nothing.
```python
class Counter:
    def __init__(self, n):
        self.n = n
        self.current = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.current >= self.n:
            raise StopIteration
        val = self.current
        self.current += 1
        return val

def two_runs(n):
    c = Counter(n)
    first = list(c)
    second = list(c)
    return first, second
```
```python
class Counter:
    def __init__(self, n):
        self.n = n

    def __iter__(self):
        current = 0
        while current < self.n:
            yield current
            current += 1

def two_runs(n):
    c = Counter(n)
    first = list(c)
    second = list(c)
    return first, second
```
```python
from module import two_runs

def test():
    first, second = two_runs(3)
    assert first == [0, 1, 2]
    assert second == [0, 1, 2]

test()
print("OK")
```
### remove_first_match_only
family: COLLECTION
difficulty: 3
why_hard: list.remove deletes only the first occurrence.
```python
def drop_all(xs, value):
    if value in xs:
        xs.remove(value)
    return xs
```
```python
def drop_all(xs, value):
    return [x for x in xs if x != value]
```
```python
from module import drop_all

def test():
    assert drop_all([1, 2, 2, 3], 2) == [1, 3]
    assert drop_all([2, 2, 2], 2) == []
    assert drop_all([], 5) == []

test()
print("OK")
```
### dict_get_default_shadow
family: CONTRACT
difficulty: 3
why_hard: Using dict.get with a mutable default and mutating it leaks shared state.
```python
def record_hit(stats, key):
    hits = stats.get(key, [])
    hits.append("hit")
    stats[key] = hits
    return stats
```
```python
def record_hit(stats, key):
    hits = stats.get(key)
    if hits is None:
        hits = []
    hits = hits + ["hit"]
    stats[key] = hits
    return stats
```
```python
from module import record_hit

def test():
    s = {}
    s1 = record_hit(s, "a")
    s2 = record_hit(s1, "a")
    assert s2["a"] == ["hit", "hit"]
    s3 = record_hit(s2, "b")
    assert s3["b"] == ["hit"]

test()
print("OK")
```
### deepcopy_missing_nested
family: DATA_FLOW
difficulty: 4
why_hard: Shallow copy leaves nested lists shared.
```python
def clone_profile(profile):
    return dict(profile)
```
```python
def clone_profile(profile):
    import copy
    return copy.deepcopy(profile)
```
```python
from module import clone_profile

def test():
    p = {"name": "x", "tags": ["a", "b"]}
    q = clone_profile(p)
    q["tags"].append("c")
    assert p["tags"] == ["a", "b"]
    assert q["tags"] == ["a", "b", "c"]

test()
print("OK")
```
### slicing_bytes_vs_ints_misuse
family: EXPRESSION_VALUE
difficulty: 3
why_hard: Treating bytes as ints and slicing incorrectly yields wrong checksums without crashing.
```python
def checksum_first_two(data):
    return sum(data[:2])
```
```python
def checksum_first_two(data):
    return data[0] + data[1]
```
```python
from module import checksum_first_two

def test():
    assert checksum_first_two(b"\x01\x02\x03") == 3
    assert checksum_first_two(b"\x10\x00") == 16

test()
print("OK")
```
### enumerate_value_misuse_as_index
family: CONTROL_FLOW
difficulty: 3
why_hard: Treating the enumerate value as the index yields wrong elements.
```python
def pick_by_indices(xs, indices):
    result = []
    for idx, x in enumerate(xs):
        if x in indices:
            result.append(x)
    return result
```
```python
def pick_by_indices(xs, indices):
    result = []
    for idx, x in enumerate(xs):
        if idx in indices:
            result.append(x)
    return result
```
```python
from module import pick_by_indices

def test():
    assert pick_by_indices(["a", "b", "c", "d"], {1, 3}) == ["b", "d"]
    assert pick_by_indices(["x"], {0}) == ["x"]

test()
print("OK")
```
### dict_items_vs_keys_confusion
family: DATA_FLOW
difficulty: 3
why_hard: Iterating .items() but assigning as if only keys swaps key/value.
```python
def invert_map(d):
    inv = {}
    for pair in d.items():
        inv[pair] = pair[0]
    return inv
```
```python
def invert_map(d):
    inv = {}
    for k, v in d.items():
        inv[v] = k
    return inv
```
```python
from module import invert_map

def test():
    d = {"a": 1, "b": 2}
    inv = invert_map(d)
    assert inv[1] == "a"
    assert inv[2] == "b"

test()
print("OK")
```
### sort_key_misapplied
family: CALL_API
difficulty: 3
why_hard: Using a sort key on the wrong attribute looks sorted but violates the criterion.
```python
def sort_tasks(tasks):
    return sorted(tasks, key=lambda t: t["name"])
```
```python
def sort_tasks(tasks):
    return sorted(tasks, key=lambda t: t["priority"])
```
```python
from module import sort_tasks

def test():
    tasks = [
        {"name": "a", "priority": 2},
        {"name": "z", "priority": 1},
        {"name": "m", "priority": 3},
    ]
    out = sort_tasks(tasks)
    assert [t["priority"] for t in out] == [1, 2, 3]

test()
print("OK")
```
### shared_iterator_in_comprehension
family: DATA_FLOW
difficulty: 4
why_hard: Using the same iterator in multiple comprehensions causes asymmetric consumption.
```python
def first_and_rest(xs):
    it = iter(xs)
    first = [next(it, None)]
    rest = [x for x in it]
    return first, rest
```
```python
def first_and_rest(xs):
    if not xs:
        return [], []
    return [xs[0]], list(xs[1:])
```
```python
from module import first_and_rest

def test():
    f, r = first_and_rest([1, 2, 3])
    assert f == [1]
    assert r == [2, 3]
    f2, r2 = first_and_rest([])
    assert f2 == []
    assert r2 == []

test()
print("OK")
```
### zip_unbalanced_pairs_silent_drop
family: COLLECTION
difficulty: 3
why_hard: Assuming zip pairs all elements hides truncated data on length mismatch.
```python
def combine(names, scores):
    return {n: s for n, s in zip(names, scores)}
```
```python
def combine(names, scores):
    if len(names) != len(scores):
        raise ValueError("names and scores length mismatch")
    return {n: s for n, s in zip(names, scores)}
```
```python
from module import combine

def test():
    assert combine(["a", "b"], [1, 2]) == {"a": 1, "b": 2}
    try:
        combine(["a"], [1, 2])
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for mismatched lengths")

test()
print("OK")
```
