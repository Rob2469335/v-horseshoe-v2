# Run tests

def run_tests():
    import unittest
    class TestBubbleSort(unittest.TestCase):
        def test_empty_list(self):
            self.assertEqual(bubble_sort([]), [])
        def test_already_sorted(self):
            self.assertEqual(bubble_sort([1]), [1])
        def test_reverse_sorted(self):
            self.assertEqual(bubble_sort([5, 2, 8, 3, 9, 4]), [2, 3, 4, 5, 8, 9])

    unittest.main()