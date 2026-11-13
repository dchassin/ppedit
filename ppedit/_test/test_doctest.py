"""Run the examples in module docstrings against the installed package"""

import doctest

import ppedit.case

def test_case_examples():
    """`ppedit.case` docstring examples reproduce their shown output"""
    failed,tried = doctest.testmod(ppedit.case)
    assert tried > 0 and failed == 0
