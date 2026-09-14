### shallow_copy_trap
family: STATE_MUTATION
difficulty: 3
why_hard: [[0]*3]*3 shares rows, so one cell write mutates a whole column.
```python
def create_grid(rows, cols):
    return [[0] * cols] * rows

def set_cell(grid, r, c, val):
    grid[r][c] = val
    return grid
```
```python
def create_grid(rows, cols):
    return [[0] * cols for _ in range(rows)]

def set_cell(grid, r, c, val):
    grid[r][c] = val
    return grid
```
```python
import module
g1 = module.create_grid(2, 2)
module.set_cell(g1, 0, 0, 9)
assert g1[1][0] == 0
g2 = module.create_grid(3, 3)
module.set_cell(g2, 2, 2, 5)
assert g2[0][2] == 0
print("OK")
```
### sort_mutation_leak
family: STATE_MUTATION
difficulty: 2
why_hard: list.sort() returns None, but fluent-API habits return its result.
```python
def get_top_scores(scores):
    return scores.sort(reverse=True)
```
```python
def get_top_scores(scores):
    scores.sort(reverse=True)
    return scores
```
```python
import module
assert module.get_top_scores([10, 30, 20]) == [30, 20, 10]
assert module.get_top_scores([5, 1]) == [5, 1]
print("OK")
```
### generator_exhaustion
family: DATA_FLOW
difficulty: 3
why_hard: the bug only manifests on the second pass; the generator is empty for the count.
```python
def analyze_sequence(seq):
    squared = (x**2 for x in seq)
    total = sum(squared)
    count = sum(1 for _ in squared)
    return total, count
```
```python
def analyze_sequence(seq):
    squared = [x**2 for x in seq]
    total = sum(squared)
    count = len(squared)
    return total, count
```
```python
import module
t1, c1 = module.analyze_sequence([1, 2, 3])
assert c1 == 3
assert t1 == 14
t2, c2 = module.analyze_sequence([4, 5])
assert c2 == 2
assert t2 == 41
print("OK")
```
### finally_overrides_return
family: CONTROL_FLOW
difficulty: 4
why_hard: a return inside finally swallows the try block's return value.
```python
def process_data(value):
    try:
        return value * 2
    finally:
        return 0
```
```python
def process_data(value):
    try:
        return value * 2
    finally:
        pass
```
```python
import module
assert module.process_data(5) == 10
assert module.process_data(10) == 20
print("OK")
```
### filter_none_truthy
family: COLLECTION
difficulty: 3
why_hard: filter(None, seq) removes ALL falsy values, not just None.
```python
def remove_nulls(data):
    return list(filter(None, data))
```
```python
def remove_nulls(data):
    return [x for x in data if x is not None]
```
```python
import module
assert module.remove_nulls([1, None, 2]) == [1, 2]
assert module.remove_nulls([0, False, None, "", 3]) == [0, False, "", 3]
print("OK")
```
### strip_character_set
family: CALL_API
difficulty: 3
why_hard: str.strip("prefix") removes ANY chars in that set, not the prefix.
```python
def remove_prefix(text, prefix):
    return text.strip(prefix)
```
```python
def remove_prefix(text, prefix):
    if text.startswith(prefix):
        return text[len(prefix):]
    return text
```
```python
import module
assert module.remove_prefix("abracadabra", "abra") == "cadabra"
assert module.remove_prefix("teatime", "tea") == "time"
print("OK")
```
### negative_floor_div
family: EXPRESSION_VALUE
difficulty: 3
why_hard: -7 // 2 is -4 (floor), not -3 (truncate toward zero).
```python
def divide_towards_zero(a, b):
    return a // b
```
```python
def divide_towards_zero(a, b):
    return int(a / b)
```
```python
import module
assert module.divide_towards_zero(7, 2) == 3
assert module.divide_towards_zero(-7, 2) == -3
print("OK")
```
### bitwise_precedence
family: EXPRESSION_VALUE
difficulty: 4
why_hard: bitwise ops bind tighter than comparisons.
```python
def is_valid_range(a, b):
    return a > 0 & b > 0
```
```python
def is_valid_range(a, b):
    return (a > 0) and (b > 0)
```
```python
import module
assert module.is_valid_range(5, 5) is True
assert module.is_valid_range(1, 0) is False
assert module.is_valid_range(0, 1) is False
print("OK")
```
### dict_iteration_mutation
family: STATE_MUTATION
difficulty: 3
why_hard: deleting keys while iterating raises RuntimeError.
```python
def clean_dict(d):
    for k, v in d.items():
        if v is None:
            del d[k]
    return d
```
```python
def clean_dict(d):
    for k in list(d.keys()):
        if d[k] is None:
            del d[k]
    return d
```
```python
import module
assert module.clean_dict({"a": 1, "b": None}) == {"a": 1}
assert module.clean_dict({"x": None, "y": None}) == {}
print("OK")
```
### late_binding_closures
family: DATA_FLOW
difficulty: 4
why_hard: closures capture by reference, so a loop of lambdas all yield the final value.
```python
def create_multipliers(n):
    return [lambda x: i * x for i in range(n)]
```
```python
def create_multipliers(n):
    return [lambda x, i=i: i * x for i in range(n)]
```
```python
import module
funcs = module.create_multipliers(3)
assert funcs[0](5) == 0
assert funcs[1](5) == 5
assert funcs[2](5) == 10
funcs2 = module.create_multipliers(2)
assert funcs2[0](10) == 0
assert funcs2[1](10) == 10
print("OK")
```
### json_keys_to_string
family: DATA_FLOW
difficulty: 3
why_hard: JSON converts int keys to strings, so int lookups fail after a round-trip.
```python
import json
def serialize_and_lookup(data_dict, key):
    serialized = json.dumps(data_dict)
    deserialized = json.loads(serialized)
    return deserialized.get(key)
```
```python
import json
def serialize_and_lookup(data_dict, key):
    serialized = json.dumps(data_dict)
    deserialized = json.loads(serialized)
    return deserialized.get(str(key))
```
```python
import module
assert module.serialize_and_lookup({1: "a", 2: "b"}, 1) == "a"
assert module.serialize_and_lookup({100: "x"}, 100) == "x"
print("OK")
```
### catch_multiple_exceptions_syntax
family: CONTROL_FLOW
difficulty: 2
why_hard: except A or B evaluates to A, so B is never caught.
```python
def safe_parse(val):
    try:
        return int(val)
    except TypeError or ValueError:
        return -1
```
```python
def safe_parse(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return -1
```
```python
import module
assert module.safe_parse(None) == -1
assert module.safe_parse("abc") == -1
assert module.safe_parse("42") == 42
print("OK")
```
### split_empty_string
family: COLLECTION
difficulty: 2
why_hard: "".split(",") returns [""] (length 1), not [].
```python
def count_tokens(text):
    tokens = text.split(",")
    return len(tokens)
```
```python
def count_tokens(text):
    if not text:
        return 0
    tokens = text.split(",")
    return len(tokens)
```
```python
import module
assert module.count_tokens("a,b,c") == 3
assert module.count_tokens("") == 0
print("OK")
```
### string_split_no_args
family: CALL_API
difficulty: 3
why_hard: s.split() drops empties and groups whitespace; s.split(" ") does not.
```python
def parse_words(sentence):
    return sentence.split(" ")
```
```python
def parse_words(sentence):
    return sentence.split()
```
```python
import module
assert module.parse_words("hello  world") == ["hello", "world"]
assert module.parse_words("   extra   spaces  ") == ["extra", "spaces"]
print("OK")
```
### groupby_unsorted
family: COLLECTION
difficulty: 4
why_hard: groupby needs sorted input or it yields fragmented groups.
```python
import itertools
def group_by_first_letter(words):
    groups = {}
    for k, g in itertools.groupby(words, key=lambda x: x[0]):
        groups[k] = list(g)
    return groups
```
```python
import itertools
def group_by_first_letter(words):
    groups = {}
    sorted_words = sorted(words, key=lambda x: x[0])
    for k, g in itertools.groupby(sorted_words, key=lambda x: x[0]):
        groups[k] = list(g)
    return groups
```
```python
import module
assert module.group_by_first_letter(["apple", "banana", "apricot"]) == {"a": ["apple", "apricot"], "b": ["banana"]}
assert module.group_by_first_letter(["cat", "dog", "cow"]) == {"c": ["cat", "cow"], "d": ["dog"]}
print("OK")
```
### collections_counter_subtract
family: CALL_API
difficulty: 3
why_hard: Counter '-' drops non-positive counts; .subtract() keeps zero/negative.
```python
from collections import Counter
def get_remaining_inventory(inv1, inv2):
    c = Counter(inv1)
    c.subtract(inv2)
    return dict(c)
```
```python
from collections import Counter
def get_remaining_inventory(inv1, inv2):
    c = Counter(inv1) - Counter(inv2)
    return dict(c)
```
```python
import module
assert module.get_remaining_inventory({"a": 3, "b": 1}, {"a": 1, "b": 1}) == {"a": 2}
assert module.get_remaining_inventory({"x": 2, "y": 2}, {"x": 3}) == {"y": 2}
print("OK")
```
### bisect_right_vs_left
family: CALL_API
difficulty: 3
why_hard: bisect defaults to bisect_right, so an exact match returns an off-by-one index.
```python
import bisect
def find_first_index(sorted_list, target):
    idx = bisect.bisect(sorted_list, target)
    if idx < len(sorted_list) and sorted_list[idx] == target:
        return idx
    return -1
```
```python
import bisect
def find_first_index(sorted_list, target):
    idx = bisect.bisect_left(sorted_list, target)
    if idx < len(sorted_list) and sorted_list[idx] == target:
        return idx
    return -1
```
```python
import module
assert module.find_first_index([1, 2, 2, 2, 3], 2) == 1
assert module.find_first_index([5, 5, 6, 7], 5) == 0
print("OK")
```
### max_empty_sequence
family: BOUNDARY
difficulty: 2
why_hard: max() on empty raises instead of returning a default.
```python
def get_max_score(scores):
    return max(scores)
```
```python
def get_max_score(scores):
    return max(scores, default=0)
```
```python
import module
assert module.get_max_score([10, 20]) == 20
assert module.get_max_score([]) == 0
print("OK")
```
### isinstance_bool_int
family: CONTRACT
difficulty: 3
why_hard: bool is a subclass of int, so the int check wins if ordered first.
```python
def encode_value(val):
    if isinstance(val, int):
        return "integer"
    elif isinstance(val, bool):
        return "boolean"
    return "other"
```
```python
def encode_value(val):
    if isinstance(val, bool):
        return "boolean"
    elif isinstance(val, int):
        return "integer"
    return "other"
```
```python
import module
assert module.encode_value(True) == "boolean"
assert module.encode_value(42) == "integer"
assert module.encode_value(False) == "boolean"
print("OK")
```
### tuple_comma_trap
family: EXPRESSION_VALUE
difficulty: 2
why_hard: a trailing comma turns a value into a 1-tuple.
```python
def get_config():
    timeout = 30,
    return timeout
```
```python
def get_config():
    timeout = 30
    return timeout
```
```python
import module
assert type(module.get_config()) is int
assert module.get_config() == 30
print("OK")
```
### regex_dot_newline
family: CALL_API
difficulty: 2
why_hard: '.' excludes newline unless re.DOTALL.
```python
import re
def extract_content(text):
    match = re.search(r"<div>(.*)</div>", text)
    return match.group(1) if match else None
```
```python
import re
def extract_content(text):
    match = re.search(r"<div>(.*)</div>", text, re.DOTALL)
    return match.group(1) if match else None
```
```python
import module
assert module.extract_content("<div>hello</div>") == "hello"
assert module.extract_content("<div>line1\nline2</div>") == "line1\nline2"
print("OK")
```
### default_dict_auto_vivification
family: STATE_MUTATION
difficulty: 4
why_hard: reading a defaultdict via d[key] on a 'check' creates the key, inflating size.
```python
from collections import defaultdict
def count_keys(operations):
    d = defaultdict(list)
    for op, key in operations:
        if op == "add":
            d[key].append(1)
        elif op == "check":
            if d[key]:
                pass
    return len(d)
```
```python
from collections import defaultdict
def count_keys(operations):
    d = defaultdict(list)
    for op, key in operations:
        if op == "add":
            d[key].append(1)
        elif op == "check":
            if key in d and d[key]:
                pass
    return len(d)
```
```python
import module
assert module.count_keys([("add", "a"), ("check", "b")]) == 1
assert module.count_keys([("check", "x"), ("check", "y")]) == 0
print("OK")
```
### list_extend_string
family: STATE_MUTATION
difficulty: 2
why_hard: extend("word") adds characters, not the string.
```python
def add_greeting(messages, name):
    messages.extend(name)
    return messages
```
```python
def add_greeting(messages, name):
    messages.append(name)
    return messages
```
```python
import module
assert module.add_greeting(["Hi"], "Bob") == ["Hi", "Bob"]
assert module.add_greeting([], "Alice") == ["Alice"]
print("OK")
```
### is_vs_equals_large_ints
family: EXPRESSION_VALUE
difficulty: 3
why_hard: 'is' checks identity; only reliable for cached small ints.
```python
def check_match(a, b):
    return a is b
```
```python
def check_match(a, b):
    return a == b
```
```python
import module
assert module.check_match(1000, 10 ** 3) is True
assert module.check_match(500, 500) is True
print("OK")
```
### math_trunc_vs_floor
family: EXPRESSION_VALUE
difficulty: 2
why_hard: trunc rounds toward zero; floor toward negative infinity.
```python
import math
def get_grid_coordinate(position):
    return math.trunc(position)
```
```python
import math
def get_grid_coordinate(position):
    return math.floor(position)
```
```python
import module
assert module.get_grid_coordinate(2.5) == 2
assert module.get_grid_coordinate(-2.5) == -3
print("OK")
```
### any_empty_sequence
family: BOUNDARY
difficulty: 2
why_hard: any([]) is False, which confuses "empty" with "no permission".
```python
def has_permission(roles):
    if not any(roles):
        return True
    return "admin" in roles
```
```python
def has_permission(roles):
    if not roles:
        return True
    return "admin" in roles
```
```python
import module
assert module.has_permission([]) is True
assert module.has_permission(["admin"]) is True
assert module.has_permission(["user"]) is False
print("OK")
```
### bool_subclass_int_arithmetic
family: EXPRESSION_VALUE
difficulty: 2
why_hard: True/False act as 1/0, so == True matches 1.
```python
def count_active(statuses):
    count = 0
    for s in statuses:
        if s == True:
            count += s
    return count
```
```python
def count_active(statuses):
    count = 0
    for s in statuses:
        if s is True:
            count += 1
    return count
```
```python
import module
assert module.count_active([True, False, True]) == 2
assert module.count_active([1, 2, True]) == 1
print("OK")
```
### replace_first_occurrence
family: CALL_API
difficulty: 2
why_hard: str.replace replaces all unless count is given.
```python
def replace_first_word(text, old, new):
    return text.replace(old, new)
```
```python
def replace_first_word(text, old, new):
    return text.replace(old, new, 1)
```
```python
import module
assert module.replace_first_word("cat cat dog", "cat", "bat") == "bat cat dog"
assert module.replace_first_word("apple banana apple", "apple", "orange") == "orange banana apple"
print("OK")
```
### index_method_not_found
family: COLLECTION
difficulty: 2
why_hard: list.index raises on absence, unlike str.find.
```python
def find_item(lst, target):
    return lst.index(target)
```
```python
def find_item(lst, target):
    try:
        return lst.index(target)
    except ValueError:
        return -1
```
```python
import module
assert module.find_item(["a", "b", "c"], "b") == 1
assert module.find_item(["a", "b", "c"], "z") == -1
print("OK")
```
### pop_empty_list
family: BOUNDARY
difficulty: 2
why_hard: list.pop raises IndexError on empty.
```python
def take_next(queue):
    return queue.pop()
```
```python
def take_next(queue):
    return queue.pop() if queue else None
```
```python
import module
assert module.take_next([1, 2]) == 2
assert module.take_next([]) is None
print("OK")
```
### set_unhashable_type
family: COLLECTION
difficulty: 3
why_hard: mutable items cannot go in a set; must preserve order without hashing.
```python
def unique_elements(lst):
    return list(set(lst))
```
```python
def unique_elements(lst):
    seen = []
    for item in lst:
        if item not in seen:
            seen.append(item)
    return seen
```
```python
import module
assert module.unique_elements([1, 2, 2]) == [1, 2]
assert module.unique_elements([[1], [2], [1]]) == [[1], [2]]
print("OK")
```
### sys_argv_zero
family: BOUNDARY
difficulty: 2
why_hard: sys.argv[0] is the script name; the first arg is argv[1].
```python
import sys
def get_input_file(args=sys.argv):
    return args[0]
```
```python
import sys
def get_input_file(args=sys.argv):
    return args[1] if len(args) > 1 else None
```
```python
import module
assert module.get_input_file(["script.py", "data.txt"]) == "data.txt"
assert module.get_input_file(["script.py"]) is None
print("OK")
```
### zip_longest_vs_zip
family: COLLECTION
difficulty: 3
why_hard: zip truncates; zip_longest retains all.
```python
def combine_lists(l1, l2):
    return [(a or 0) + (b or 0) for a, b in zip(l1, l2)]
```
```python
import itertools
def combine_lists(l1, l2):
    return [(a or 0) + (b or 0) for a, b in itertools.zip_longest(l1, l2)]
```
```python
import module
assert module.combine_lists([1, 2], [3, 4]) == [4, 6]
assert module.combine_lists([1, 2, 3], [10]) == [11, 2, 3]
print("OK")
```
### sorted_dict_keys
family: COLLECTION
difficulty: 2
why_hard: sorted(dict) returns keys, losing values.
```python
def get_sorted_items(d):
    return sorted(d)
```
```python
def get_sorted_items(d):
    return sorted(d.items())
```
```python
import module
assert module.get_sorted_items({"b": 2, "a": 1}) == [("a", 1), ("b", 2)]
assert module.get_sorted_items({"z": 10}) == [("z", 10)]
print("OK")
```
### class_mutable_default
family: STATE_MUTATION
difficulty: 4
why_hard: mutable default constructor args are shared across instances.
```python
class Manager:
    def __init__(self, items=[]):
        self.items = items
    def add(self, x):
        self.items.append(x)
```
```python
class Manager:
    def __init__(self, items=None):
        self.items = items if items is not None else []
    def add(self, x):
        self.items.append(x)
```
```python
import module
m1 = module.Manager()
m1.add(1)
m2 = module.Manager()
m2.add(2)
assert m1.items == [1]
assert m2.items == [2]
print("OK")
```
### boolean_short_circuit_eval
family: CONTROL_FLOW
difficulty: 3
why_hard: 'or' skips the second call's side effects when the first is truthy.
```python
def process(a_func, b_func):
    return a_func() or b_func()
```
```python
def process(a_func, b_func):
    res_a = a_func()
    res_b = b_func()
    return res_a or res_b
```
```python
import module
side_effects = []
def f1():
    side_effects.append("a")
    return True
def f2():
    side_effects.append("b")
    return False
assert module.process(f1, f2) is True
assert side_effects == ["a", "b"]
side_effects.clear()
assert module.process(lambda: False, f2) is False
assert side_effects == ["b"]
print("OK")
```
### re_match_vs_search
family: CALL_API
difficulty: 2
why_hard: re.match anchors at start; re.search scans the string.
```python
import re
def contains_number(text):
    return bool(re.match(r"\d+", text))
```
```python
import re
def contains_number(text):
    return bool(re.search(r"\d+", text))
```
```python
import module
assert module.contains_number("123 abc") is True
assert module.contains_number("abc 123") is True
print("OK")
```
### float_addition_associativity
family: EXPRESSION_VALUE
difficulty: 3
why_hard: 0.1 + 0.2 == 0.3 is False due to float drift.
```python
def sum_matches(a, b, expected):
    return a + b == expected
```
```python
import math
def sum_matches(a, b, expected):
    return math.isclose(a + b, expected)
```
```python
import module
assert module.sum_matches(1.0, 2.0, 3.0) is True
assert module.sum_matches(0.1, 0.2, 0.3) is True
print("OK")
```
### dict_pop_default
family: BOUNDARY
difficulty: 2
why_hard: dict.pop raises KeyError without a default.
```python
def extract_id(data):
    return data.pop("id")
```
```python
def extract_id(data):
    return data.pop("id", None)
```
```python
import module
assert module.extract_id({"id": 42, "val": 1}) == 42
assert module.extract_id({"val": 2}) is None
print("OK")
```
### dict_keys_not_list
family: BOUNDARY
difficulty: 2
why_hard: dict.keys() is a view, not indexable.
```python
def get_first_key(d):
    return d.keys()[0]
```
```python
def get_first_key(d):
    return list(d.keys())[0] if d else None
```
```python
import module
assert module.get_first_key({"a": 1, "b": 2}) == "a"
assert module.get_first_key({}) is None
print("OK")
```
### round_half_even
family: EXPRESSION_VALUE
difficulty: 2
why_hard: Python uses banker's rounding; round(2.5) == 2.
```python
def round_up_half(val):
    return round(val)
```
```python
import math
def round_up_half(val):
    return math.floor(val + 0.5)
```
```python
import module
assert module.round_up_half(2.5) == 3
assert module.round_up_half(3.5) == 4
print("OK")
```
### datetime_naive_aware_cmp
family: CONTRACT
difficulty: 3
why_hard: comparing naive and aware datetimes raises TypeError.
```python
from datetime import datetime, timezone
def is_past(dt):
    return dt < datetime.now()
```
```python
from datetime import datetime, timezone
def is_past(dt):
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return dt < now
```
```python
import module
from datetime import datetime, timezone, timedelta
past = datetime.now() - timedelta(days=1)
past_aware = datetime.now(timezone.utc) - timedelta(days=1)
assert module.is_past(past) is True
assert module.is_past(past_aware) is True
print("OK")
```
### set_discard_vs_remove
family: CALL_API
difficulty: 2
why_hard: set.remove raises on absence; discard does not.
```python
def remove_target(items_set, target):
    items_set.remove(target)
    return items_set
```
```python
def remove_target(items_set, target):
    items_set.discard(target)
    return items_set
```
```python
import module
assert module.remove_target({1, 2}, 1) == {2}
assert module.remove_target({1, 2}, 3) == {1, 2}
print("OK")
```
### join_non_strings
family: CALL_API
difficulty: 2
why_hard: str.join raises TypeError on non-string elements.
```python
def format_csv(items):
    return ",".join(items)
```
```python
def format_csv(items):
    return ",".join(str(x) for x in items)
```
```python
import module
assert module.format_csv(["a", "b"]) == "a,b"
assert module.format_csv([1, 2, 3]) == "1,2,3"
print("OK")
```
### string_islower_empty
family: BOUNDARY
difficulty: 2
why_hard: "".islower() is False, breaking logic that expects True for no-uppercase strings.
```python
def is_all_lowercase(text):
    return text.islower()
```
```python
def is_all_lowercase(text):
    if not text:
        return True
    return text.islower()
```
```python
import module
assert module.is_all_lowercase("abc") is True
assert module.is_all_lowercase("") is True
assert module.is_all_lowercase("A") is False
print("OK")
```
