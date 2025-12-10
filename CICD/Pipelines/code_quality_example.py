#!/usr/bin/env python3
"""
Example Python file demonstrating various code quality issues.

This file is designed to trigger different quality checks in the pipeline,
helping developers understand what issues the tools detect and why they matter.
"""

# Issue: Unsorted imports (isort)
import os
import json
from typing import Optional, List, Dict
import sys
from datetime import datetime
import requests
import random

# Issue: Unused imports (Ruff/Flake8)
import math  # noqa: F401
import collections

# Issue: Wrong import order (should be stdlib, third-party, local)
from .utils import helper_function


# Issue: Missing type hints (MyPy)
def calculate_average(numbers):
    """Calculate average without type hints."""
    # Issue: No input validation
    return sum(numbers) / len(numbers)


# Issue: Too complex function (Cyclomatic complexity > 10)
def process_data(data, mode, validate=True, transform=None):
    # Issue: Missing docstring details
    result = []

    # Issue: Too many nested conditions
    if data:
        if mode == "simple":
            for item in data:
                if validate:
                    if item > 0:
                        if item < 100:
                            result.append(item)
                        else:
                            print(f"Item {item} too large")  # Issue: print instead of logging
                    else:
                        print(f"Item {item} is negative")
                else:
                    result.append(item)
        elif mode == "complex":
            for item in data:
                if transform:
                    if transform == "square":
                        result.append(item ** 2)
                    elif transform == "cube":
                        result.append(item ** 3)
                    elif transform == "sqrt":
                        result.append(item ** 0.5)
                    else:
                        result.append(item)
                else:
                    result.append(item * 2)
        elif mode == "advanced":
            # Issue: Duplicate code
            for item in data:
                if validate:
                    if item > 0:
                        if item < 100:
                            result.append(item * 3)
                        else:
                            print(f"Item {item} too large")
                    else:
                        print(f"Item {item} is negative")
                else:
                    result.append(item * 3)
    else:
        # Issue: Using generic Exception
        raise Exception("No data provided")  # Issue: Should use specific exception

    return result


# Issue: Class without proper docstring
class DataProcessor:
    # Issue: Class variable without type annotation
    default_timeout = 30

    # Issue: Missing type hints in __init__
    def __init__(self, name, config=None):
        self.name = name
        self.config = config or {}
        # Issue: Hardcoded values
        self.api_key = "sk-1234567890abcdef"  # Issue: Security - hardcoded secret
        self.password = "admin123"  # Issue: Security - hardcoded password

    # Issue: Method too long (> 50 lines)
    def process(self, input_data):
        """Process input data."""
        # Issue: No input validation
        results = []
        errors = []
        warnings = []

        # Issue: Using eval (security risk)
        if "expression" in self.config:
            result = eval(self.config["expression"])  # Issue: Security vulnerability

        # Issue: SQL injection vulnerability
        if "query" in self.config:
            query = f"SELECT * FROM users WHERE name = '{input_data}'"  # Issue: SQL injection

        # Issue: Using assert in production code
        assert input_data is not None, "Input data cannot be None"

        # Issue: Too many local variables
        temp1 = input_data.get("field1")
        temp2 = input_data.get("field2")
        temp3 = input_data.get("field3")
        temp4 = input_data.get("field4")
        temp5 = input_data.get("field5")
        temp6 = input_data.get("field6")
        temp7 = input_data.get("field7")
        temp8 = input_data.get("field8")
        temp9 = input_data.get("field9")
        temp10 = input_data.get("field10")

        # Issue: Magic numbers
        if temp1 > 42:
            results.append(temp1 * 3.14159)

        # Issue: Broad exception handling
        try:
            # Issue: Using os.system (security risk)
            os.system(f"echo {input_data}")  # Issue: Command injection
        except:  # Issue: Bare except clause
            pass  # Issue: Silently ignoring errors

        # Issue: Not using context manager for file operations
        file = open("data.txt", "r")  # Issue: Resource leak
        content = file.read()
        # Issue: Forgetting to close file

        # Issue: Using global variable
        global some_global_var
        some_global_var = "modified"

        # Issue: Comparison with None using ==
        if input_data == None:  # Issue: Should use 'is None'
            return None

        # Issue: Using type() instead of isinstance()
        if type(input_data) == dict:  # Issue: Should use isinstance()
            pass

        # Issue: Mutable default argument
        def helper(items=[]):  # Issue: Mutable default argument
            items.append("new")
            return items

        return results

    # Issue: Method without return type hint
    def validate(self, data: Dict):
        """Validate data but missing return type."""
        # Issue: Inconsistent return types
        if not data:
            return False
        if "required_field" not in data:
            return "Missing required field"  # Issue: Returning string instead of bool
        return True

    # Issue: Dead code (unreachable)
    def unused_method(self):
        """This method is never called."""
        return "dead code"

    # Issue: Function with too many arguments
    def complex_method(self, arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8):
        """Method with too many parameters."""
        pass


# Issue: Missing class docstring
class AnotherClass:
    pass  # Issue: Empty class could be removed


# Issue: Function at module level doing I/O
def global_side_effect():
    """Function with side effects at module level."""
    with open("config.json", "r") as f:
        return json.load(f)


# Issue: Calling function at module level
config = global_side_effect()  # Issue: Side effect at import time


# Issue: Using deprecated approach
def old_style_string_formatting(name, age):
    # Issue: Using % formatting instead of f-strings
    return "Name: %s, Age: %d" % (name, age)


# Issue: Not following naming conventions
def calculateSum(Numbers):  # Issue: camelCase and PascalCase
    Total = 0  # Issue: PascalCase for variable
    for NUMBER in Numbers:  # Issue: UPPERCASE for regular variable
        Total += NUMBER
    return Total


# Issue: Line too long (> 88 characters)
def function_with_very_long_line():
    """Function with a line that exceeds the recommended maximum line length of 88 characters according to Black formatter."""
    very_long_variable_name_that_makes_the_line_exceed_the_maximum_recommended_length = "This is a very long string that continues and continues"
    return very_long_variable_name_that_makes_the_line_exceed_the_maximum_recommended_length


# Issue: Cognitive complexity too high
def overly_complex_logic(a, b, c, d):
    """Function with high cognitive complexity."""
    if a:
        if b:
            if c:
                if d:
                    return "nested"
                else:
                    for i in range(10):
                        if i % 2 == 0:
                            for j in range(5):
                                if j > 2:
                                    print(i, j)
            else:
                while c < 10:
                    c += 1
                    if c == 5:
                        break
        else:
            try:
                if not b and not c:
                    raise ValueError()
            except ValueError:
                if d:
                    return "error"
    return None


# Issue: TODO/FIXME comments (should be tracked)
# TODO: Implement this function
# FIXME: This is broken
# HACK: Temporary workaround
def incomplete_function():
    """Function with TODO comments."""
    # BUG: This doesn't work correctly
    pass


# Issue: Trailing whitespace
# Issue: Mixed indentation (tabs and spaces)
def badly_formatted():
	"""Function with formatting issues."""
	x = 1  # Tab indentation
        y = 2  # Space indentation (inconsistent)
	return x + y


# Issue: Missing final newline
if __name__ == "__main__":
    print("This file demonstrates various code quality issues")
    # Issue: Exit without cleanup
    sys.exit(0)
