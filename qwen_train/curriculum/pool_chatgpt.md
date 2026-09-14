### early_return_after_update
family: CONTROL_FLOW
difficulty: 3
why_hard: performs the right update but returns before processing the remaining input.
```python
def summarize(values):
    total = 0
    for value in values:
        total += value
        return total
```
```python
def summarize(values):
    total = 0
    for value in values:
        total += value
    return total
```
```python
from module import summarize
assert summarize([2, 3, 4]) == 9
assert summarize([5, 1, 7, 2]) == 15
print("OK")
```
### misplaced_break
family: CONTROL_FLOW
difficulty: 3
why_hard: the break terminates a search at the first non-match instead of after a successful match.
```python
def first_even(values):
    for value in values:
        if value % 2 == 0:
            return value
        break
    return None
```
```python
def first_even(values):
    for value in values:
        if value % 2 == 0:
            return value
    return None
```
```python
from module import first_even
assert first_even([3, 7, 8, 10]) == 8
assert first_even([5, 9, 11, 14]) == 14
print("OK")
```
### continue_skips_state
family: CONTROL_FLOW
difficulty: 3
why_hard: a continue prevents required state from being updated for one class of elements.
```python
def score(values):
    total = 0
    for value in values:
        if value < 0:
            continue
        total += value
    return total
```
```python
def score(values):
    total = 0
    for value in values:
        if value < 0:
            total += abs(value)
        else:
            total += value
    return total
```
```python
from module import score
assert score([3, -2, 5]) == 10
assert score([-4, 6, -1]) == 11
print("OK")
```
### stale_accumulator
family: DATA_FLOW
difficulty: 3
why_hard: the accumulator retains the wrong per-group state across iterations.
```python
def group_totals(groups):
    total = 0
    result = []
    for group in groups:
        for value in group:
            total += value
        result.append(total)
    return result
```
```python
def group_totals(groups):
    result = []
    for group in groups:
        total = 0
        for value in group:
            total += value
        result.append(total)
    return result
```
```python
from module import group_totals
assert group_totals([[1, 2], [4]]) == [3, 4]
assert group_totals([[5], [2, 3], [7, 1]]) == [5, 5, 8]
print("OK")
```
### accumulator_alias
family: DATA_FLOW
difficulty: 4
why_hard: two names refer to the same mutable accumulator, creating cross-result contamination.
```python
def split_totals(values):
    positives = negatives = []
    for value in values:
        if value >= 0:
            positives.append(value)
        else:
            negatives.append(value)
    return sum(positives), sum(negatives)
```
```python
def split_totals(values):
    positives, negatives = [], []
    for value in values:
        if value >= 0:
            positives.append(value)
        else:
            negatives.append(value)
    return sum(positives), sum(negatives)
```
```python
from module import split_totals
assert split_totals([3, -2, 5]) == (8, -2)
assert split_totals([-4, 6, -1]) == (6, -5)
print("OK")
```
### shallow_copy_mutation
family: STATE_MUTATION
difficulty: 4
why_hard: a shallow copy protects the outer container but not nested mutable objects.
```python
def increment_first(rows):
    copied = rows.copy()
    copied[0][0] += 1
    return rows, copied
```
```python
def increment_first(rows):
    copied = [row.copy() for row in rows]
    copied[0][0] += 1
    return rows, copied
```
```python
from module import increment_first
original, changed = increment_first([[2, 3], [4, 5]])
assert original == [[2, 3], [4, 5]]
assert changed == [[3, 3], [4, 5]]
original, changed = increment_first([[7], [9]])
assert original == [[7], [9]]
assert changed == [[8], [9]]
print("OK")
```
### destructive_iteration
family: STATE_MUTATION
difficulty: 4
why_hard: removing elements while iterating changes which elements are visited.
```python
def remove_zeros(values):
    for value in values:
        if value == 0:
            values.remove(value)
    return values
```
```python
def remove_zeros(values):
    return [value for value in values if value != 0]
```
```python
from module import remove_zeros
assert remove_zeros([0, 1, 0, 2]) == [1, 2]
assert remove_zeros([3, 0, 0, 4, 0]) == [3, 4]
print("OK")
```
### alias_return_mutation
family: STATE_MUTATION
difficulty: 4
why_hard: returning a caller-owned mutable object and then mutating it makes the side effect invisible.
```python
def add_marker(items):
    result = items
    result.append("done")
    return result
```
```python
def add_marker(items):
    result = items.copy()
    result.append("done")
    return result
```
```python
from module import add_marker
a = ["a"]
b = add_marker(a)
assert a == ["a"]
assert b == ["a", "done"]
c = [1, 2]
d = add_marker(c)
assert c == [1, 2]
assert d == [1, 2, "done"]
print("OK")
```
### state_leak_between_calls
family: STATE_MUTATION
difficulty: 4
why_hard: function state survives between calls even though each invocation appears independent.
```python
_cache = []
def remember(value):
    _cache.append(value)
    return _cache
```
```python
def remember(value):
    cache = []
    cache.append(value)
    return cache
```
```python
from module import remember
assert remember("a") == ["a"]
assert remember("b") == ["b"]
print("OK")
```
### mutation_order_dependency
family: STATE_MUTATION
difficulty: 4
why_hard: the output depends on mutating one structure before another operation that expects the original.
```python
def pair_with_first(values):
    first = values.pop(0)
    return [(first, value) for value in values]
```
```python
def pair_with_first(values):
    first = values[0]
    return [(first, value) for value in values[1:]]
```
```python
from module import pair_with_first
assert pair_with_first([10, 20, 30]) == [(10, 20), (10, 30)]
assert pair_with_first(["a", "b"]) == [("a", "b")]
print("OK")
```
### wrong_precondition_scope
family: CONTROL_FLOW
difficulty: 4
why_hard: the guard is applied to the whole operation when it should apply only to one branch.
```python
def describe(value, detailed):
    if value is None or not detailed:
        return "basic"
    if detailed:
        return f"value={value}"
    return "basic"
```
```python
def describe(value, detailed):
    if value is None:
        return "basic"
    if detailed:
        return f"value={value}"
    return "basic"
```
```python
from module import describe
assert describe(7, True) == "value=7"
assert describe(7, False) == "basic"
print("OK")
```
### wrong_branch_order
family: CONTROL_FLOW
difficulty: 4
why_hard: both branches are sensible, but their order makes a more-specific case unreachable.
```python
def classify(value):
    if value >= 0:
        return "nonnegative"
    if value == 0:
        return "zero"
    return "negative"
```
```python
def classify(value):
    if value == 0:
        return "zero"
    if value >= 0:
        return "nonnegative"
    return "negative"
```
```python
from module import classify
assert classify(0) == "zero"
assert classify(3) == "nonnegative"
print("OK")
```
### missing_else_state
family: CONTROL_FLOW
difficulty: 3
why_hard: the function initializes a valid-looking value but fails to update it for the complementary branch.
```python
def sign_name(value):
    result = "positive"
    if value == 0:
        result = "zero"
    return result
```
```python
def sign_name(value):
    if value == 0:
        return "zero"
    if value < 0:
        return "negative"
    return "positive"
```
```python
from module import sign_name
assert sign_name(-3) == "negative"
assert sign_name(4) == "positive"
print("OK")
```
### wrong_guard_combination
family: BOUNDARY
difficulty: 4
why_hard: combining guards with the wrong grouping changes which boundary cases are accepted.
```python
def valid_age(age):
    return 0 <= age < 13 or age > 65
```
```python
def valid_age(age):
    return 0 <= age < 13 or 65 <= age
```
```python
from module import valid_age
assert valid_age(65) is True
assert valid_age(64) is False
print("OK")
```
### boundary_inclusive_exclusive
family: BOUNDARY
difficulty: 4
why_hard: the implementation chooses the wrong interval convention.
```python
def in_window(value):
    return 10 < value < 20
```
```python
def in_window(value):
    return 10 <= value < 20
```
```python
from module import in_window
assert in_window(10) is True
assert in_window(11) is True
print("OK")
```
### nested_boundary_interaction
family: BOUNDARY
difficulty: 4
why_hard: a boundary condition mishandles the interaction between an outer and inner boundary.
```python
def take_middle(values):
    if len(values) < 2:
        return []
    return values[1:-1]
```
```python
def take_middle(values):
    if len(values) < 3:
        return []
    return values[1:-1]
```
```python
from module import take_middle
assert take_middle([1, 2]) == []
assert take_middle([1, 2, 3]) == [2]
print("OK")
```
### none_empty_collision
family: BOUNDARY
difficulty: 4
why_hard: None and an empty value are distinct states but the implementation collapses them.
```python
def label(value):
    if not value:
        return "missing"
    return "present"
```
```python
def label(value):
    if value is None:
        return "missing"
    return "present"
```
```python
from module import label
assert label("") == "present"
assert label(None) == "missing"
print("OK")
```
### zero_empty_collision
family: BOUNDARY
difficulty: 3
why_hard: truthiness makes a valid numeric zero indistinguishable from an absent value.
```python
def amount(value):
    if not value:
        return 100
    return value
```
```python
def amount(value):
    if value is None:
        return 100
    return value
```
```python
from module import amount
assert amount(0) == 0
assert amount(25) == 25
print("OK")
```
### first_last_alias
family: COLLECTION
difficulty: 3
why_hard: the function accidentally uses the same endpoint for two semantically different positions.
```python
def endpoints(values):
    return values[0], values[0]
```
```python
def endpoints(values):
    return values[0], values[-1]
```
```python
from module import endpoints
assert endpoints([3, 8, 11]) == (3, 11)
assert endpoints(["a", "b"]) == ("a", "b")
print("OK")
```
### reverse_direction
family: COLLECTION
difficulty: 3
why_hard: the operation is valid but traverses a sequence in the opposite semantic direction.
```python
def newest(values):
    return values[0]
```
```python
def newest(values):
    return values[-1]
```
```python
from module import newest
assert newest(["old", "mid", "new"]) == "new"
assert newest([2, 4]) == 4
print("OK")
```
### duplicate_suppression_loss
family: COLLECTION
difficulty: 4
why_hard: preserves values but silently changes the required multiplicity semantics.
```python
def doubled(values):
    return list(set(values))
```
```python
def doubled(values):
    return list(values)
```
```python
from module import doubled
assert doubled([1, 1, 2]) == [1, 1, 2]
assert doubled(["a", "a", "b"]) == ["a", "a", "b"]
print("OK")
```
### order_preservation_loss
family: COLLECTION
difficulty: 4
why_hard: the returned collection has the right members but loses an observable ordering contract.
```python
def unique(values):
    return list(set(values))
```
```python
def unique(values):
    return list(dict.fromkeys(values))
```
```python
from module import unique
assert unique([3, 1, 3, 2]) == [3, 1, 2]
assert unique(["b", "a", "b"]) == ["b", "a"]
print("OK")
```
### wrong_dict_source
family: DATA_FLOW
difficulty: 3
why_hard: two mappings contain similarly named data, making the wrong source plausible.
```python
def price(name, catalog, discounts):
    return discounts[name]
```
```python
def price(name, catalog, discounts):
    return catalog[name] - discounts.get(name, 0)
```
```python
from module import price
assert price("a", {"a": 100}, {"a": 20}) == 80
assert price("b", {"b": 50}, {}) == 50
print("OK")
```
### wrong_update_target
family: DATA_FLOW
difficulty: 4
why_hard: the code updates a valid variable of the right type, but not the variable later returned.
```python
def count_kinds(values):
    numbers = 0
    strings = 0
    for value in values:
        if isinstance(value, int):
            strings += 1
        else:
            strings += 1
    return numbers, strings
```
```python
def count_kinds(values):
    numbers = 0
    strings = 0
    for value in values:
        if isinstance(value, int):
            numbers += 1
        else:
            strings += 1
    return numbers, strings
```
```python
from module import count_kinds
assert count_kinds([1, "a", 2]) == (2, 1)
assert count_kinds(["x", "y"]) == (0, 2)
print("OK")
```
### wrong_intermediate_value
family: DATA_FLOW
difficulty: 4
why_hard: one intermediate value is derived from the wrong stage of the computation.
```python
def average_positive(values):
    total = sum(values)
    positives = [v for v in values if v > 0]
    return total / len(positives)
```
```python
def average_positive(values):
    positives = [v for v in values if v > 0]
    return sum(positives) / len(positives)
```
```python
from module import average_positive
assert average_positive([2, -2, 4]) == 3
assert average_positive([-3, 6, 2]) == 4
print("OK")
```
### wrong_filter_before_map
family: DATA_FLOW
difficulty: 4
why_hard: mapping and filtering do not commute here, so the wrong ordering passes superficial inspection.
```python
def positive_squares(values):
    return [x * x for x in values if x * x > 4]
```
```python
def positive_squares(values):
    return [x * x for x in values if x > 0 and x * x > 4]
```
```python
from module import positive_squares
assert positive_squares([-4, 2, 3]) == [4, 9]
assert positive_squares([-5, 1, 4]) == [1, 16]
print("OK")
```
### sign_lost_before_operation
family: EXPRESSION_VALUE
difficulty: 4
why_hard: an intermediate transformation removes information the later calculation needs.
```python
def distance(a, b):
    a = abs(a)
    return abs(a - b)
```
```python
def distance(a, b):
    return abs(a - b)
```
```python
from module import distance
assert distance(-5, -2) == 3
assert distance(-2, 5) == 7
print("OK")
```
### integer_division_semantics
family: EXPRESSION_VALUE
difficulty: 3
why_hard: both division forms are valid and often produce the same result on friendly inputs.
```python
def midpoint(a, b):
    return (a + b) // 2
```
```python
def midpoint(a, b):
    return (a + b) / 2
```
```python
from module import midpoint
assert midpoint(1, 2) == 1.5
assert midpoint(4, 7) == 5.5
print("OK")
```
### wrong_sign_normalization
family: EXPRESSION_VALUE
difficulty: 3
why_hard: computes a magnitude correctly for positive inputs while mishandling the intended signed result.
```python
def delta(a, b):
    return abs(a - b)
```
```python
def delta(a, b):
    return a - b
```
```python
from module import delta
assert delta(9, 4) == 5
assert delta(4, 9) == -5
print("OK")
```
### distributive_parentheses
family: EXPRESSION_VALUE
difficulty: 4
why_hard: applies multiplication to only one operand instead of the intended group.
```python
def scale_total(a, b, factor):
    return a + b * factor
```
```python
def scale_total(a, b, factor):
    return (a + b) * factor
```
```python
from module import scale_total
assert scale_total(2, 3, 4) == 20
assert scale_total(5, 1, 3) == 18
print("OK")
```
### boolean_numeric_confusion
family: EXPRESSION_VALUE
difficulty: 4
why_hard: Python permits booleans in numeric contexts, so the incorrect implementation stays type-valid.
```python
def active_count(values):
    return sum(values)
```
```python
def active_count(values):
    return sum(1 for value in values if value is True)
```
```python
from module import active_count
assert active_count([True, False, True]) == 2
assert active_count([1, True, False]) == 1
print("OK")
```
### wrong_abs_location
family: EXPRESSION_VALUE
difficulty: 4
why_hard: moving a normalization to the wrong side of an expression changes behavior only for mixed signs.
```python
def gap(a, b):
    return abs(a) - abs(b)
```
```python
def gap(a, b):
    return abs(a - b)
```
```python
from module import gap
assert gap(-5, 2) == 7
assert gap(3, -4) == 7
print("OK")
```
### wrong_string_pipeline
family: CALL_API
difficulty: 4
why_hard: each string operation is legitimate, but the order of transformations changes the result.
```python
def clean(text):
    return text.lower().replace("-", " ").strip()
```
```python
def clean(text):
    return text.strip().replace("-", " ").lower()
```
```python
from module import clean
assert clean("  Hello-World  ") == "hello world"
assert clean("A-B-C") == "a b c"
print("OK")
```
### wrong_method_receiver
family: CALL_API
difficulty: 4
why_hard: the correct method exists on both values, making the receiver mistake plausible.
```python
def contains(prefix, text):
    return prefix.startswith(text)
```
```python
def contains(prefix, text):
    return text.startswith(prefix)
```
```python
from module import contains
assert contains("pre", "prefix") is True
assert contains("fix", "prefix") is False
print("OK")
```
### wrong_call_result_used
family: CALL_API
difficulty: 4
why_hard: a mutating API returns None while the useful value is in the mutated object.
```python
def sorted_values(values):
    result = values.sort()
    return result
```
```python
def sorted_values(values):
    result = values.copy()
    result.sort()
    return result
```
```python
from module import sorted_values
assert sorted_values([3, 1, 2]) == [1, 2, 3]
assert sorted_values([5, 4]) == [4, 5]
print("OK")
```
### wrong_api_mutability
family: CALL_API
difficulty: 4
why_hard: two APIs have nearly identical surface behavior but one mutates the caller.
```python
def sorted_copy(values):
    values.sort()
    return values
```
```python
def sorted_copy(values):
    return sorted(values)
```
```python
from module import sorted_copy
a = [3, 1, 2]
assert sorted_copy(a) == [1, 2, 3]
assert a == [3, 1, 2]
b = [2, 1]
assert sorted_copy(b) == [1, 2]
assert b == [2, 1]
print("OK")
```
### default_factory_per_call
family: CONTRACT
difficulty: 4
why_hard: the default container is created once rather than once per invocation.
```python
def add(value, bucket=[]):
    bucket.append(value)
    return bucket
```
```python
def add(value, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(value)
    return bucket
```
```python
from module import add
assert add(1) == [1]
assert add(2) == [2]
print("OK")
```
### return_alias
family: CONTRACT
difficulty: 4
why_hard: returns an internal mutable object whose later mutation changes a previously observed result.
```python
def build(values):
    result = []
    result.extend(values)
    return result
```
```python
def build(values):
    return tuple(values)
```
```python
from module import build
assert build([1, 2]) == (1, 2)
assert build(["a", "b"]) == ("a", "b")
print("OK")
```
### exception_contract
family: CONTRACT
difficulty: 4
why_hard: converts an expected failure mode into a silent sentinel, changing the observable API contract.
```python
def parse_number(text):
    try:
        return int(text)
    except ValueError:
        return None
```
```python
def parse_number(text):
    return int(text)
```
```python
from module import parse_number
assert parse_number("12") == 12
try:
    parse_number("x")
except ValueError:
    pass
else:
    raise AssertionError("ValueError expected")
print("OK")
```
### sentinel_collision
family: CONTRACT
difficulty: 4
why_hard: a legitimate data value is reused as the signal for absence.
```python
def find(values, target):
    for value in values:
        if value == target:
            return None
    return None
```
```python
def find(values, target):
    for value in values:
        if value == target:
            return value
    return None
```
```python
from module import find
assert find([3, 5, 8], 5) == 5
assert find([3, 5, 8], 9) is None
print("OK")
```
### contract_shape
family: CONTRACT
difficulty: 4
why_hard: contents are correct but the function returns the wrong structural shape.
```python
def stats(values):
    return sum(values), len(values)
```
```python
def stats(values):
    return {"total": sum(values), "count": len(values)}
```
```python
from module import stats
assert stats([2, 3]) == {"total": 5, "count": 2}
assert stats([5, 5, 5]) == {"total": 15, "count": 3}
print("OK")
```
### wrong_none_propagation
family: BOUNDARY
difficulty: 4
why_hard: substitutes a concrete value where the surrounding API expects absence to propagate.
```python
def first_or_none(values):
    if values:
        return values[0]
    return 0
```
```python
def first_or_none(values):
    if values:
        return values[0]
    return None
```
```python
from module import first_or_none
assert first_or_none([]) is None
assert first_or_none([0]) == 0
print("OK")
```
### nested_none_propagation
family: BOUNDARY
difficulty: 4
why_hard: a nested lookup can legitimately produce None, but the code treats that as a trigger.
```python
def get_name(records, key):
    value = records.get(key)
    if not value:
        return "unknown"
    return value["name"]
```
```python
def get_name(records, key):
    value = records.get(key)
    if value is None:
        return "unknown"
    return value["name"]
```
```python
from module import get_name
data = {"a": {"name": ""}, "b": {"name": "Bob"}}
assert get_name(data, "a") == ""
assert get_name(data, "b") == "Bob"
print("OK")
```
### wrong_comprehension_scope
family: DATA_FLOW
difficulty: 4
why_hard: a comprehension references a variable whose meaning differs from the intended outer-loop value.
```python
def pair_counts(groups):
    return [(group, len(groups)) for group in groups]
```
```python
def pair_counts(groups):
    return [(group, len(group)) for group in groups]
```
```python
from module import pair_counts
assert pair_counts([[1], [2, 3]]) == [([1], 1), ([2, 3], 2)]
assert pair_counts([["a", "b", "c"], ["x"]]) == [(["a", "b", "c"], 3), (["x"], 1)]
print("OK")
```
### wrong_nested_accumulation
family: DATA_FLOW
difficulty: 4
why_hard: inner and outer aggregation levels produce the wrong semantic unit.
```python
def row_sums(matrix):
    total = 0
    result = []
    for row in matrix:
        total += sum(row)
        result.append(total)
    return result
```
```python
def row_sums(matrix):
    result = []
    for row in matrix:
        result.append(sum(row))
    return result
```
```python
from module import row_sums
assert row_sums([[1, 2], [3, 4]]) == [3, 7]
assert row_sums([[5], [2, 3], [1]]) == [5, 5, 1]
print("OK")
```
### wrong_partition_boundary
family: BOUNDARY
difficulty: 4
why_hard: the partition looks symmetrical but assigns the boundary element to the wrong side.
```python
def partition(values, pivot):
    return [v for v in values if v < pivot], [v for v in values if v >= pivot]
```
```python
def partition(values, pivot):
    return [v for v in values if v <= pivot], [v for v in values if v > pivot]
```
```python
from module import partition
assert partition([1, 3, 5], 3) == ([1, 3], [5])
assert partition([2, 4, 6], 4) == ([2, 4], [6])
print("OK")
```
### wrong_range_source
family: BOUNDARY
difficulty: 4
why_hard: the loop bound is derived from the wrong collection, failing only when lengths differ.
```python
def combine(a, b):
    result = []
    for i in range(len(a)):
        result.append(a[i] + b[i])
    return result
```
```python
def combine(a, b):
    result = []
    for i in range(len(b)):
        result.append(a[i] + b[i])
    return result
```
```python
from module import combine
assert combine([10, 20], [1, 2]) == [11, 22]
assert combine([10, 20, 30], [1, 2]) == [11, 22]
print("OK")
```
### conditional_accumulator_reset
family: STATE_MUTATION
difficulty: 4
why_hard: resetting state on the wrong branch makes only multi-phase inputs reveal the defect.
```python
def runs(values):
    result = []
    total = 0
    for value in values:
        if value == 0:
            result.append(total)
        else:
            total += value
    return result
```
```python
def runs(values):
    result = []
    total = 0
    for value in values:
        if value == 0:
            result.append(total)
            total = 0
        else:
            total += value
    return result
```
```python
from module import runs
assert runs([2, 3, 0, 4, 0]) == [5, 4]
assert runs([1, 0, 2, 3, 0]) == [1, 5]
print("OK")
```
### state_updated_after_read
family: STATE_MUTATION
difficulty: 4
why_hard: the variable is updated only after the output captured its stale value.
```python
def running_totals(values):
    total = 0
    result = []
    for value in values:
        result.append(total)
        total += value
    return result
```
```python
def running_totals(values):
    total = 0
    result = []
    for value in values:
        total += value
        result.append(total)
    return result
```
```python
from module import running_totals
assert running_totals([2, 3, 4]) == [2, 5, 9]
assert running_totals([5, 1]) == [5, 6]
print("OK")
```
### cached_derived_value
family: STATE_MUTATION
difficulty: 4
why_hard: a derived value is computed once even though its source changes during the loop.
```python
def weighted(values):
    factor = len(values)
    result = []
    for value in values:
        result.append(value * factor)
        factor -= 1
    return result
```
```python
def weighted(values):
    result = []
    for index, value in enumerate(values):
        result.append(value * (len(values) - index))
    return result
```
```python
from module import weighted
assert weighted([2, 3, 4]) == [6, 6, 4]
assert weighted([5, 1]) == [10, 1]
print("OK")
```
### wrong_phase_order
family: CONTROL_FLOW
difficulty: 4
why_hard: both phases are correct but running them in the wrong order changes the data.
```python
def normalize(values):
    values = [x * 2 for x in values]
    return sorted(values)
```
```python
def normalize(values):
    values = sorted(values)
    return [x * 2 for x in values]
```
```python
from module import normalize
assert normalize([3, 1, 2]) == [2, 4, 6]
assert normalize([5, 2]) == [4, 10]
print("OK")
```
### wrong_postprocessing_order
family: CALL_API
difficulty: 4
why_hard: same transformations reordered, so a token-focused model can miss the semantic dependency.
```python
def format_name(first, last):
    return first.strip() + " " + last.upper().strip()
```
```python
def format_name(first, last):
    return first.strip().upper() + " " + last.strip().upper()
```
```python
from module import format_name
assert format_name(" bob ", "smith ") == "BOB SMITH"
assert format_name(" Ann", "Jones") == "ANN JONES"
print("OK")
```
### conditional_expression_precedence
family: EXPRESSION_VALUE
difficulty: 4
why_hard: the conditional expression binds differently than the surrounding arithmetic suggests.
```python
def adjust(value, enabled):
    return value + 10 if enabled else 0
```
```python
def adjust(value, enabled):
    return value + (10 if enabled else 0)
```
```python
from module import adjust
assert adjust(5, True) == 15
assert adjust(5, False) == 5
print("OK")
```
### comparison_chain_semantics
family: EXPRESSION_VALUE
difficulty: 4
why_hard: chained comparisons are not equivalent to comparing against the same reference inclusively.
```python
def between(value, low, high):
    return low < value < high
```
```python
def between(value, low, high):
    return low <= value <= high
```
```python
from module import between
assert between(3, 3, 5) is True
assert between(5, 3, 5) is True
print("OK")
```
### wrong_unpacking_role
family: DATA_FLOW
difficulty: 4
why_hard: unpacking succeeds, but the names are assigned opposite semantic roles.
```python
def format_pair(pair):
    last, first = pair
    return f"{first} {last}"
```
```python
def format_pair(pair):
    first, last = pair
    return f"{first} {last}"
```
```python
from module import format_pair
assert format_pair(("Ada", "Lovelace")) == "Ada Lovelace"
assert format_pair(("Grace", "Hopper")) == "Grace Hopper"
print("OK")
```
### wrong_dict_update_order
family: STATE_MUTATION
difficulty: 4
why_hard: both updates are correct individually, but their order decides which duplicate key wins.
```python
def merge(base, override):
    result = override.copy()
    result.update(base)
    return result
```
```python
def merge(base, override):
    result = base.copy()
    result.update(override)
    return result
```
```python
from module import merge
assert merge({"a": 1}, {"a": 2, "b": 3}) == {"a": 2, "b": 3}
assert merge({"x": 5}, {"x": 9}) == {"x": 9}
print("OK")
```
### wrong_set_operation
family: COLLECTION
difficulty: 3
why_hard: union, intersection, and difference all return valid sets.
```python
def common(a, b):
    return set(a) | set(b)
```
```python
def common(a, b):
    return set(a) & set(b)
```
```python
from module import common
assert common([1, 2, 3], [2, 3, 4]) == {2, 3}
assert common(["a", "b"], ["b", "c"]) == {"b"}
print("OK")
```
### wrong_dict_fallback
family: CALL_API
difficulty: 3
why_hard: works for present keys but uses the wrong fallback for absent keys.
```python
def get_level(data, name):
    return data.get(name, 1)
```
```python
def get_level(data, name):
    return data.get(name, 0)
```
```python
from module import get_level
assert get_level({"a": 3}, "a") == 3
assert get_level({"a": 3}, "b") == 0
print("OK")
```
### wrong_recursive_subproblem
family: DATA_FLOW
difficulty: 4
why_hard: the recursive call is structurally correct but passes the wrong reduced problem.
```python
def countdown_total(n):
    if n <= 0:
        return 0
    return n + countdown_total(n)
```
```python
def countdown_total(n):
    if n <= 0:
        return 0
    return n + countdown_total(n - 1)
```
```python
from module import countdown_total
assert countdown_total(3) == 6
assert countdown_total(5) == 15
print("OK")
```
### recursive_base_state
family: BOUNDARY
difficulty: 4
why_hard: the recursive reduction is right but the base case returns the wrong identity value.
```python
def product_to(n):
    if n <= 1:
        return 0
    return n * product_to(n - 1)
```
```python
def product_to(n):
    if n <= 1:
        return 1
    return n * product_to(n - 1)
```
```python
from module import product_to
assert product_to(1) == 1
assert product_to(4) == 24
print("OK")
```
### recursive_branch_asymmetry
family: CONTROL_FLOW
difficulty: 4
why_hard: one recursive branch is correct while the other silently skips required work.
```python
def tree_sum(tree):
    if tree is None:
        return 0
    value, left, right = tree
    return value + tree_sum(left)
```
```python
def tree_sum(tree):
    if tree is None:
        return 0
    value, left, right = tree
    return value + tree_sum(left) + tree_sum(right)
```
```python
from module import tree_sum
assert tree_sum((1, (2, None, None), (3, None, None))) == 6
assert tree_sum((5, (2, (1, None, None), None), (4, None, None))) == 12
print("OK")
```
### closure_captured_state
family: STATE_MUTATION
difficulty: 4
why_hard: a closure captures mutable state that should be isolated between factory calls.
```python
_state = []
def make_adder():
    def add(value):
        _state.append(value)
        return sum(_state)
    return add
```
```python
def make_adder():
    state = []
    def add(value):
        state.append(value)
        return sum(state)
    return add
```
```python
from module import make_adder
a = make_adder()
b = make_adder()
assert a(2) == 2
assert b(5) == 5
assert a(3) == 5
print("OK")
```
### iterator_consumption
family: STATE_MUTATION
difficulty: 4
why_hard: an iterator is consumed by an earlier operation, leaving a later operation empty.
```python
def first_and_total(values):
    first = next(values)
    total = first + sum(values)
    return first, total
```
```python
def first_and_total(values):
    values = list(values)
    first = values[0]
    return first, sum(values)
```
```python
from module import first_and_total
assert first_and_total(iter([2, 3, 4])) == (2, 9)
assert first_and_total(iter([5, 1])) == (5, 6)
print("OK")
```
### generator_reuse
family: STATE_MUTATION
difficulty: 4
why_hard: the same generator is consumed twice, so the second computation sees an empty iterator.
```python
def stats(values):
    gen = (x * 2 for x in values)
    return sum(gen), list(gen)
```
```python
def stats(values):
    data = [x * 2 for x in values]
    return sum(data), data
```
```python
from module import stats
assert stats([1, 2, 3]) == (12, [2, 4, 6])
assert stats([4, 5]) == (18, [8, 10])
print("OK")
```
### wrong_enumeration_index
family: BOUNDARY
difficulty: 3
why_hard: the enumeration is correct but the index is interpreted as one-based.
```python
def labeled(values):
    return [f"{i}:{value}" for i, value in enumerate(values)]
```
```python
def labeled(values):
    return [f"{i + 1}:{value}" for i, value in enumerate(values)]
```
```python
from module import labeled
assert labeled(["a", "b"]) == ["1:a", "2:b"]
assert labeled(["x", "y", "z"]) == ["1:x", "2:y", "3:z"]
print("OK")
```
### wrong_length_after_filter
family: DATA_FLOW
difficulty: 4
why_hard: the numerator is filtered correctly but the denominator describes the original population.
```python
def positive_average(values):
    positive = [x for x in values if x > 0]
    return sum(positive) / len(values)
```
```python
def positive_average(values):
    positive = [x for x in values if x > 0]
    return sum(positive) / len(positive)
```
```python
from module import positive_average
assert positive_average([2, -2, 4]) == 3
assert positive_average([-1, 6, 2]) == 4
print("OK")
```
### wrong_partition_recombination
family: COLLECTION
difficulty: 4
why_hard: the partitions are correct individually but the recombination changes ordering.
```python
def negatives_first(values):
    negatives = [x for x in values if x < 0]
    others = [x for x in values if x >= 0]
    return negatives + others
```
```python
def negatives_first(values):
    negatives = [x for x in values if x < 0]
    others = [x for x in values if x >= 0]
    return others + negatives
```
```python
from module import negatives_first
assert negatives_first([1, -2, 3]) == [1, 3, -2]
assert negatives_first([-1, 4, -3]) == [4, -1, -3]
print("OK")
```
