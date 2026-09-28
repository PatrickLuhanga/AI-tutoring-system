"""Synthetic Java code-repair corpus.

A hand-authored dataset of 50 common beginner Java mistakes. Each entry feeds
the ``code_repair_patterns`` vector table and is retrieved (module-filtered) by
the RAG Orchestrator when a student submits broken code. Fields required by the
specification are present on every record:

    * ``broken_code``          - the failing snippet
    * ``exception_thrown``     - the compiler/runtime error the student sees
    * ``conceptual_tutor_hint``- a Socratic hint that guides without giving the fix

Extra fields (``error_category``, ``common_cause``, ``tags``, ``difficulty``)
exist to support retrieval quality and tutor analytics.
"""

from __future__ import annotations

from typing import Any, Dict, List

JAVA_LANGUAGE = "Java"
DEFAULT_MODULE_ID = "IPRT301"


JAVA_ERROR_CORPUS: List[Dict[str, Any]] = [
    {
        "error_title": "NullPointerException calling a method on a null reference",
        "error_category": "null_pointer",
        "exception_thrown": "java.lang.NullPointerException",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String name = null;
        System.out.println(name.length());
    }
}""",
        "conceptual_tutor_hint": "Stop at the line that fails. What object is `name` pointing to when `length()` is called? Ask yourself where, if anywhere, the code ever assigns a real object to it.",
        "common_cause": "A reference variable is declared but never initialised with `new` (or a value), then dereferenced.",
        "tags": ["null", "NullPointerException", "references"],
        "difficulty": "beginner",
    },
    {
        "error_title": "ArrayIndexOutOfBoundsException reading past the last element",
        "error_category": "array_bounds",
        "exception_thrown": "java.lang.ArrayIndexOutOfBoundsException: Index 3 out of bounds for length 3",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int[] marks = {10, 20, 30};
        System.out.println(marks[3]);
    }
}""",
        "conceptual_tutor_hint": "An array of three elements has valid indexes starting where? Count them out loud. Which index holds the last element, and is that the index you used?",
        "common_cause": "Assuming arrays are one-indexed, or confusing the length (3) with the highest valid index (2).",
        "tags": ["array", "index", "off-by-one"],
        "difficulty": "beginner",
    },
    {
        "error_title": "StringIndexOutOfBoundsException using charAt past the end",
        "error_category": "string_bounds",
        "exception_thrown": "java.lang.StringIndexOutOfBoundsException: String index out of range: 4",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String s = "Java";
        char c = s.charAt(4);
        System.out.println(c);
    }
}""",
        "conceptual_tutor_hint": "How many characters are in the string, and which index does the final character occupy? What is the largest index that `charAt` can accept here?",
        "common_cause": "Reusing an index from a longer string, or treating length as the last index.",
        "tags": ["String", "charAt", "bounds"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Missing semicolon at end of a statement",
        "error_category": "syntax",
        "exception_thrown": "Compilation error: ';' expected",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int x = 5
        System.out.println(x);
    }
}""",
        "conceptual_tutor_hint": "The compiler points at the line after the mistake. Look at the end of the statement on the previous line. How does Java know a statement has finished?",
        "common_cause": "Forgetting the terminating semicolon (common when translating from Python).",
        "tags": ["syntax", "semicolon"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Mismatched curly braces causing end-of-file parse error",
        "error_category": "syntax",
        "exception_thrown": "Compilation error: reached end of file while parsing",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        if (true) {
            System.out.println("Hi");
    }
}""",
        "conceptual_tutor_hint": "Every `{` needs a partner `}`. Trace the braces from the outside in and count how many are still open when the file ends. Which block never closed?",
        "common_cause": "Copy-pasting a block and forgetting its closing brace, or deleting a brace while editing.",
        "tags": ["syntax", "braces"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Assigning an int to a String reference",
        "error_category": "type_mismatch",
        "exception_thrown": "Compilation error: incompatible types: int cannot be converted to String",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int age = 21;
        String text = age;
        System.out.println(text);
    }
}""",
        "conceptual_tutor_hint": "What is the type on the left of the `=` and what is the type on the right? Java is statically typed. What operation do you already know that turns a value into text?",
        "common_cause": "Assuming numbers auto-convert to text, as in dynamically typed languages.",
        "tags": ["types", "String", "conversion"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Cannot find symbol due to a misspelled variable name",
        "error_category": "name_resolution",
        "exception_thrown": "Compilation error: cannot find symbol - variable total",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        System.out.println("Total: " + total);
        int totle = 100;
    }
}""",
        "conceptual_tutor_hint": "Read the two identifiers character by character. Are they exactly the same word, and is the variable declared before it is used?",
        "common_cause": "Typos, or using a variable before its declaration line.",
        "tags": ["symbol", "typo", "scope"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Public class name does not match the file name",
        "error_category": "class_structure",
        "exception_thrown": "Compilation error: class Program is public, should be declared in a file named Program.java",
        "broken_code": """// file saved as Main.java
public class Program {
    public static void main(String[] args) {
        System.out.println("Hello");
    }
}""",
        "conceptual_tutor_hint": "Look at the public class name and the file name. What rule links the two in Java, and which one should you change?",
        "common_cause": "Renaming the class in the editor but not renaming the file (or vice versa).",
        "tags": ["class", "filename", "public"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Missing return statement in a method with a return type",
        "error_category": "method_signature",
        "exception_thrown": "Compilation error: missing return statement",
        "broken_code": """public class Main {
    public static int square(int n) {
        int result = n * n;
    }
}""",
        "conceptual_tutor_hint": "The method promises to hand back an `int`. Follow the code path to the closing brace: where does it actually hand anything back?",
        "common_cause": "Computing a value but forgetting to `return` it.",
        "tags": ["return", "method"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Main method declared without static",
        "error_category": "method_signature",
        "exception_thrown": "Error: Main method not found in class Main, please define the main method as: public static void main(String[] args)",
        "broken_code": """public class Main {
    public void main(String[] args) {
        System.out.println("Start");
    }
}""",
        "conceptual_tutor_hint": "The runtime looks for a specific method signature to start your program. Compare your `main` to the exact signature the error message prints. What keyword is missing?",
        "common_cause": "Dropping the `static` modifier from the entry point.",
        "tags": ["main", "static", "entry_point"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Calling a non-static method from a static context",
        "error_category": "scoping",
        "exception_thrown": "Compilation error: non-static method greet() cannot be referenced from a static context",
        "broken_code": """public class Main {
    public void greet() {
        System.out.println("Hi");
    }

    public static void main(String[] args) {
        greet();
    }
}""",
        "conceptual_tutor_hint": "`main` belongs to the class, not to an object. What must exist before you can call an instance method? How could you create that thing inside `main`?",
        "common_cause": "Calling instance methods directly from `main` without an object.",
        "tags": ["static", "instance", "method"],
        "difficulty": "beginner",
    },
    {
        "error_title": "NumberFormatException parsing non-numeric text",
        "error_category": "runtime_exception",
        "exception_thrown": "java.lang.NumberFormatException: For input string: \"12a\"",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String input = "12a";
        int n = Integer.parseInt(input);
        System.out.println(n);
    }
}""",
        "conceptual_tutor_hint": "What exactly was the method asked to convert? Look at every character of the string. Which character cannot be part of a whole number?",
        "common_cause": "Passing text with spaces, letters or symbols to `parseInt`.",
        "tags": ["parseInt", "input", "NumberFormatException"],
        "difficulty": "beginner",
    },
    {
        "error_title": "ArithmeticException dividing an integer by zero",
        "error_category": "runtime_exception",
        "exception_thrown": "java.lang.ArithmeticException: / by zero",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int total = 10;
        int groups = 0;
        int average = total / groups;
        System.out.println(average);
    }
}""",
        "conceptual_tutor_hint": "Before the division runs, what value does `groups` hold? Is dividing an integer by zero ever legal in Java? What should happen instead?",
        "common_cause": "A denominator that becomes zero because of a calculation or bad input.",
        "tags": ["division", "ArithmeticException", "logic"],
        "difficulty": "beginner",
    },
    {
        "error_title": "ClassCastException casting a String to Integer",
        "error_category": "runtime_exception",
        "exception_thrown": "java.lang.ClassCastException: class java.lang.String cannot be cast to class java.lang.Integer",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        Object value = "hello";
        Integer n = (Integer) value;
        System.out.println(n);
    }
}""",
        "conceptual_tutor_hint": "A cast does not change what an object is; it only asserts what it already is. What is the real runtime type stored in `value`?",
        "common_cause": "Assuming a cast converts types instead of re-checking the existing type.",
        "tags": ["casting", "ClassCastException", "object"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "ArrayStoreException storing the wrong type in an object array",
        "error_category": "runtime_exception",
        "exception_thrown": "java.lang.ArrayStoreException: java.lang.String",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        Object[] items = new Integer[3];
        items[0] = "text";
        System.out.println(items[0]);
    }
}""",
        "conceptual_tutor_hint": "Read the array creation expression carefully. What concrete type did `new` actually build, and what type are you now trying to store?",
        "common_cause": "Declaring the reference as a supertype but instantiating a subtype array.",
        "tags": ["array", "ArrayStoreException", "polymorphism"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "InputMismatchException reading a non-integer with Scanner",
        "error_category": "runtime_exception",
        "exception_thrown": "java.util.InputMismatchException",
        "broken_code": """import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.print("Age: ");
        int age = sc.nextInt();
        System.out.println(age);
    }
}""",
        "conceptual_tutor_hint": "`nextInt()` expects the next token to be an integer. If the user typed a word, what token did it receive? How could you inspect the input as text first?",
        "common_cause": "Not validating user input before requesting a specific primitive type.",
        "tags": ["Scanner", "input", "InputMismatchException"],
        "difficulty": "beginner",
    },
    {
        "error_title": "NoSuchElementException reading more tokens than exist",
        "error_category": "runtime_exception",
        "exception_thrown": "java.util.NoSuchElementException",
        "broken_code": """import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner("42");
        int a = sc.nextInt();
        int b = sc.nextInt();
        System.out.println(a + b);
    }
}""",
        "conceptual_tutor_hint": "How many tokens does the scanner actually have? Count how many times you ask it for a value before it runs out.",
        "common_cause": "Assuming there is always another token; forgetting `hasNextInt()` guards.",
        "tags": ["Scanner", "NoSuchElementException", "input"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Unhandled FileNotFoundException from FileReader",
        "error_category": "checked_exception",
        "exception_thrown": "Compilation error: unreported exception java.io.FileNotFoundException; must be caught or declared to be thrown",
        "broken_code": """import java.io.FileReader;

public class Main {
    public static void main(String[] args) {
        FileReader reader = new FileReader("data.txt");
    }
}""",
        "conceptual_tutor_hint": "The compiler is telling you this exception cannot be ignored. What are the two ways Java lets you deal with a checked exception?",
        "common_cause": "Forgetting that I/O operations throw checked exceptions.",
        "tags": ["IOException", "checked", "try-catch"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Possible lossy conversion from double to int",
        "error_category": "type_mismatch",
        "exception_thrown": "Compilation error: incompatible types: possible lossy conversion from double to int",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        double price = 19.99;
        int rounded = price;
        System.out.println(rounded);
    }
}""",
        "conceptual_tutor_hint": "What information lives in the fractional part of `price`? If Java silently dropped it, would that be safe? What explicit operation or rounding method should you use?",
        "common_cause": "Narrowing a floating-point value without an explicit cast or rounding.",
        "tags": ["types", "double", "int", "casting"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Unreachable statement after a return",
        "error_category": "control_flow",
        "exception_thrown": "Compilation error: unreachable statement",
        "broken_code": """public class Main {
    public static int check() {
        return 1;
        System.out.println("done");
    }
}""",
        "conceptual_tutor_hint": "Once `return` executes, where does control go? Can any line after it in the same block ever run?",
        "common_cause": "Leaving debug output or logic after a `return`.",
        "tags": ["unreachable", "return", "control-flow"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Variable might not have been initialized before use",
        "error_category": "initialization",
        "exception_thrown": "Compilation error: variable score might not have been initialized",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int score;
        if (score > 50) {
            System.out.println("Pass");
        }
    }
}""",
        "conceptual_tutor_hint": "Local variables in Java are not given a default value. You are reading `score` before ever writing to it. Where should it first receive a value?",
        "common_cause": "Confusing field defaults with local variable rules.",
        "tags": ["initialization", "local-variable", "compile-error"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Instantiating an abstract class",
        "error_category": "oop",
        "exception_thrown": "Compilation error: Shape is abstract; cannot be instantiated",
        "broken_code": """abstract class Shape {
    abstract double area();
}

public class Main {
    public static void main(String[] args) {
        Shape s = new Shape();
    }
}""",
        "conceptual_tutor_hint": "An abstract class may leave some behaviour undefined. What does that tell you about creating objects directly from it? Which concrete subclass should be instantiated instead?",
        "common_cause": "Treating an abstract base as if it were a complete implementation.",
        "tags": ["abstract", "inheritance", "oop"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Incompatible return type when overriding a method",
        "error_category": "oop",
        "exception_thrown": "Compilation error: legs() in Spider cannot override legs() in Animal; return type String is not compatible with int",
        "broken_code": """class Animal {
    int legs() {
        return 4;
    }
}

class Spider extends Animal {
    String legs() {
        return "eight";
    }
}""",
        "conceptual_tutor_hint": "An overriding method must be a drop-in replacement. If a caller expects an `int` back, can it cope with a `String`? What does that mean for the return type?",
        "common_cause": "Changing the return type while overriding instead of overriding correctly.",
        "tags": ["override", "inheritance", "return-type"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Adding an int to an ArrayList<String>",
        "error_category": "generics",
        "exception_thrown": "Compilation error: incompatible types: int cannot be converted to String",
        "broken_code": """import java.util.ArrayList;

public class Main {
    public static void main(String[] args) {
        ArrayList<String> names = new ArrayList<>();
        names.add(42);
    }
}""",
        "conceptual_tutor_hint": "What did you promise this list holds when you wrote the angle brackets? Check the type of the value you are adding against that promise.",
        "common_cause": "Ignoring the element type declared in the generic parameter.",
        "tags": ["generics", "ArrayList", "types"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Comparing Strings with == instead of equals",
        "error_category": "logic_error",
        "exception_thrown": "Logic error (no exception): prints false for two equal-looking Strings",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String a = new String("hi");
        String b = new String("hi");
        System.out.println(a == b);
    }
}""",
        "conceptual_tutor_hint": "For objects, what does `==` actually compare? Do `a` and `b` necessarily point to the same object in memory? Which method compares the characters instead?",
        "common_cause": "Using reference equality on objects that should be compared by value.",
        "tags": ["String", "equals", "comparison"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Integer division truncating the decimal result",
        "error_category": "logic_error",
        "exception_thrown": "Logic error (no exception): result is 3.0 instead of 3.5",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int total = 7;
        int count = 2;
        double average = total / count;
        System.out.println(average);
    }
}""",
        "conceptual_tutor_hint": "The division happens before the assignment. What type are `total` and `count`? What does Java do when it divides two integers?",
        "common_cause": "Dividing two ints and expecting a fractional result.",
        "tags": ["division", "types", "logic"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Infinite while loop because the counter never changes",
        "error_category": "logic_error",
        "exception_thrown": "No exception: program hangs and never terminates",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int i = 0;
        while (i < 5) {
            System.out.println(i);
        }
    }
}""",
        "conceptual_tutor_hint": "Look at the condition and then look inside the loop body. What value must change for the condition to eventually become false, and where is that change?",
        "common_cause": "Forgetting to increment the loop counter.",
        "tags": ["loop", "while", "infinite-loop"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Off-by-one loop condition exceeding the array length",
        "error_category": "array_bounds",
        "exception_thrown": "java.lang.ArrayIndexOutOfBoundsException: Index 3 out of bounds for length 3",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int[] data = {1, 2, 3};
        for (int i = 0; i <= data.length; i++) {
            System.out.println(data[i]);
        }
    }
}""",
        "conceptual_tutor_hint": "Trace the last iteration by hand. What value does `i` reach, and what index does that access when the array has three elements?",
        "common_cause": "Using `<=` with `.length` instead of `<`.",
        "tags": ["for-loop", "length", "off-by-one"],
        "difficulty": "beginner",
    },
    {
        "error_title": "ConcurrentModificationException removing during iteration",
        "error_category": "collections",
        "exception_thrown": "java.util.ConcurrentModificationException",
        "broken_code": """import java.util.ArrayList;
import java.util.List;

public class Main {
    public static void main(String[] args) {
        List<String> list = new ArrayList<>(List.of("a", "b"));
        for (String s : list) {
            if (s.equals("a")) {
                list.remove(s);
            }
        }
    }
}""",
        "conceptual_tutor_hint": "The for-each loop uses an iterator behind the scenes. What happens if the collection changes underneath that iterator? Which removal method is safe instead?",
        "common_cause": "Modifying a collection through its own methods while iterating it.",
        "tags": ["collections", "iterator", "ConcurrentModificationException"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "NullPointerException unboxing a null Integer",
        "error_category": "null_pointer",
        "exception_thrown": "java.lang.NullPointerException",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        Integer count = null;
        int total = count + 1;
        System.out.println(total);
    }
}""",
        "conceptual_tutor_hint": "`count` is a wrapper object, not a primitive. Before the addition can happen, what must Java do to it, and does a null object support that?",
        "common_cause": "Wrapper objects that are null being auto-unboxed.",
        "tags": ["autoboxing", "null", "Integer"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "StringIndexOutOfBoundsException from a backwards substring range",
        "error_category": "string_bounds",
        "exception_thrown": "java.lang.StringIndexOutOfBoundsException: begin 3, end 1, length 5",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String s = "hello";
        System.out.println(s.substring(3, 1));
    }
}""",
        "conceptual_tutor_hint": "`substring(begin, end)` expects the begin index to come before the end index. Compare the two numbers you passed. Which is larger?",
        "common_cause": "Swapping the begin and end arguments.",
        "tags": ["substring", "bounds", "String"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Using an array variable before allocating it with new",
        "error_category": "initialization",
        "exception_thrown": "Compilation error: variable nums might not have been initialized",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int[] nums;
        nums[0] = 5;
        System.out.println(nums[0]);
    }
}""",
        "conceptual_tutor_hint": "Declaring an array reference does not create the array itself. What keyword and size are needed to actually reserve the elements before you assign index 0?",
        "common_cause": "Confusing array declaration with array instantiation.",
        "tags": ["array", "new", "initialization"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Cannot find symbol ArrayList because java.util is not imported",
        "error_category": "imports",
        "exception_thrown": "Compilation error: cannot find symbol - class ArrayList",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        ArrayList<String> list = new ArrayList<>();
        list.add("hello");
        System.out.println(list);
    }
}""",
        "conceptual_tutor_hint": "The compiler knows the name `ArrayList` but not where it lives. Which package provides it, and what line at the top tells the compiler to look there?",
        "common_cause": "Using a library class without its import statement.",
        "tags": ["import", "ArrayList", "package"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Duplicate local variable declaration",
        "error_category": "scoping",
        "exception_thrown": "Compilation error: variable x is already defined in method main",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int x = 1;
        int x = 2;
        System.out.println(x);
    }
}""",
        "conceptual_tutor_hint": "Within the same block, how many variables may share one name? Which line should either be removed or renamed?",
        "common_cause": "Re-declaring a variable with `int` instead of reassigning it.",
        "tags": ["scope", "variable", "redeclaration"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Assigning a new value to a final variable",
        "error_category": "constants",
        "exception_thrown": "Compilation error: cannot assign a value to final variable MAX",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        final int MAX = 10;
        MAX = 20;
        System.out.println(MAX);
    }
}""",
        "conceptual_tutor_hint": "What does the keyword `final` guarantee about a variable once it has a value? Which of the two assignments must be the only one?",
        "common_cause": "Trying to change a constant after initialisation.",
        "tags": ["final", "constants", "assignment"],
        "difficulty": "beginner",
    },
    {
        "error_title": "IllegalFormatConversionException using %d with a double",
        "error_category": "formatting",
        "exception_thrown": "java.util.IllegalFormatConversionException: d != java.lang.Double",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        System.out.printf("Age: %d%n", 21.5);
    }
}""",
        "conceptual_tutor_hint": "The format specifier and the argument must agree. `%d` is for whole numbers. What specifier matches a decimal value?",
        "common_cause": "Mismatching printf conversions and argument types.",
        "tags": ["printf", "format", "specifier"],
        "difficulty": "beginner",
    },
    {
        "error_title": "StackOverflowError from recursion without a base case",
        "error_category": "recursion",
        "exception_thrown": "java.lang.StackOverflowError",
        "broken_code": """public class Main {
    public static int fact(int n) {
        return n * fact(n - 1);
    }

    public static void main(String[] args) {
        System.out.println(fact(5));
    }
}""",
        "conceptual_tutor_hint": "Every recursive method needs a stopping condition. What is the smallest input, and what should the method return for it without calling itself?",
        "common_cause": "Omitting the base case in a recursive method.",
        "tags": ["recursion", "StackOverflowError", "base-case"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "OutOfMemoryError adding to a list inside a non-terminating loop",
        "error_category": "memory",
        "exception_thrown": "java.lang.OutOfMemoryError: Java heap space",
        "broken_code": """import java.util.ArrayList;
import java.util.List;

public class Main {
    public static void main(String[] args) {
        List<Integer> list = new ArrayList<>();
        while (true) {
            list.add(1);
        }
    }
}""",
        "conceptual_tutor_hint": "The loop condition never becomes false, and each iteration keeps a reference to new data. What happens to memory over time? What should bound the loop?",
        "common_cause": "Unbounded loops that accumulate objects which can never be collected.",
        "tags": ["memory", "OutOfMemoryError", "loop"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "NegativeArraySizeException creating an array with a negative size",
        "error_category": "runtime_exception",
        "exception_thrown": "java.lang.NegativeArraySizeException",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int n = -5;
        int[] arr = new int[n];
        System.out.println(arr.length);
    }
}""",
        "conceptual_tutor_hint": "What value does `n` hold just before the array is created? Can an array have a negative number of elements? Where did that negative value come from?",
        "common_cause": "A size computed from input or arithmetic that can go negative.",
        "tags": ["array", "NegativeArraySizeException", "size"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Scanner nextLine returning empty after nextInt",
        "error_category": "input",
        "exception_thrown": "Logic error (no exception): the name is read as an empty String",
        "broken_code": """import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        System.out.print("Age: ");
        int age = sc.nextInt();
        System.out.print("Name: ");
        String name = sc.nextLine();
        System.out.println(age + " " + name);
    }
}""",
        "conceptual_tutor_hint": "`nextInt()` reads only the number and stops before the newline. What is left in the buffer, and what does `nextLine()` therefore read first?",
        "common_cause": "Mixing token-based and line-based Scanner methods.",
        "tags": ["Scanner", "nextLine", "input"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Accessing a private field from another class",
        "error_category": "encapsulation",
        "exception_thrown": "Compilation error: balance has private access in Account",
        "broken_code": """class Account {
    private double balance;
}

public class Main {
    public static void main(String[] args) {
        Account acc = new Account();
        System.out.println(acc.balance);
    }
}""",
        "conceptual_tutor_hint": "What is the purpose of the `private` keyword? If the field is hidden deliberately, what controlled way of reading it should the class provide?",
        "common_cause": "Bypassing encapsulation instead of using a getter.",
        "tags": ["private", "encapsulation", "access"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Constructor accidentally given a return type",
        "error_category": "oop",
        "exception_thrown": "Compilation error: constructor Car in class Car cannot be applied to given types; required: no arguments",
        "broken_code": """class Car {
    int speed;

    void Car(int s) {
        speed = s;
    }
}

public class Main {
    public static void main(String[] args) {
        Car c = new Car(60);
    }
}""",
        "conceptual_tutor_hint": "A constructor has no return type. What did the `void` keyword turn this method into, and why does `new Car(60)` then fail to find a matching constructor?",
        "common_cause": "Adding `void` (or any return type) to a constructor declaration.",
        "tags": ["constructor", "void", "oop"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Abstract method declared in a non-abstract class",
        "error_category": "oop",
        "exception_thrown": "Compilation error: Vehicle is not abstract and does not override abstract method move()",
        "broken_code": """class Vehicle {
    abstract void move();
}""",
        "conceptual_tutor_hint": "A class that contains an unimplemented method cannot itself be complete. Which keyword must be added to the class declaration to acknowledge that?",
        "common_cause": "Declaring an abstract method without marking the enclosing class abstract.",
        "tags": ["abstract", "class", "oop"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Class does not implement an interface method",
        "error_category": "oop",
        "exception_thrown": "Compilation error: Welcome is not abstract and does not override abstract method greet() in Greeter",
        "broken_code": """interface Greeter {
    void greet();
}

class Welcome implements Greeter {
}

public class Main {
    public static void main(String[] args) {
        Greeter g = new Welcome();
    }
}""",
        "conceptual_tutor_hint": "Implementing an interface is a promise to provide every method it declares. Which method did you agree to write but have not yet defined?",
        "common_cause": "Forgetting to implement all interface methods after adding `implements`.",
        "tags": ["interface", "implements", "abstract"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Switch fall-through producing unexpected output",
        "error_category": "control_flow",
        "exception_thrown": "Logic error (no exception): both the midweek and almost-weekend messages print",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int day = 2;
        switch (day) {
            case 1:
            case 2:
                System.out.println("Midweek");
            case 3:
                System.out.println("Almost");
        }
    }
}""",
        "conceptual_tutor_hint": "After a matching `case` finishes its statements, where does control go if nothing stops it? What statement stops the fall-through when a case is done?",
        "common_cause": "Omitting the `break` statement in a switch case.",
        "tags": ["switch", "break", "fall-through"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Calling equals on a null reference",
        "error_category": "null_pointer",
        "exception_thrown": "java.lang.NullPointerException",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String input = null;
        if (input.equals("yes")) {
            System.out.println("ok");
        }
    }
}""",
        "conceptual_tutor_hint": "Which side of the `.equals` call must be non-null for the method to run at all? How can you reorder the comparison or guard it?",
        "common_cause": "Invoking a method on a null reference during comparison.",
        "tags": ["equals", "null", "comparison"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Integer overflow on arithmetic past the maximum value",
        "error_category": "logic_error",
        "exception_thrown": "Logic error (no exception): prints -2147483648 instead of 2147483648",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        int max = Integer.MAX_VALUE;
        System.out.println(max + 1);
    }
}""",
        "conceptual_tutor_hint": "What is the largest value an `int` can hold? What happens when you add one more to a value already at that limit? Which wider type avoids this?",
        "common_cause": "Exceeding the range of an int without using long/BigInteger.",
        "tags": ["overflow", "int", "range"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Casting a String directly to int",
        "error_category": "type_mismatch",
        "exception_thrown": "Compilation error: incompatible types: String cannot be converted to int",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        String count = "10";
        int total = (int) count;
        System.out.println(total);
    }
}""",
        "conceptual_tutor_hint": "A cast only works between compatible types. Text and numbers are unrelated types here. Which method parses a numeric string into an int?",
        "common_cause": "Confusing a cast with parsing a string value.",
        "tags": ["casting", "parseInt", "String"],
        "difficulty": "beginner",
    },
    {
        "error_title": "Adding to a fixed-size list from Arrays.asList",
        "error_category": "collections",
        "exception_thrown": "java.lang.UnsupportedOperationException",
        "broken_code": """import java.util.Arrays;
import java.util.List;

public class Main {
    public static void main(String[] args) {
        List<String> names = Arrays.asList("a", "b");
        names.add("c");
        System.out.println(names);
    }
}""",
        "conceptual_tutor_hint": "`Arrays.asList` returns a list backed by the original array, which has a fixed length. What kind of list can actually grow, and how would you build one?",
        "common_cause": "Assuming the list returned by Arrays.asList is fully mutable.",
        "tags": ["collections", "UnsupportedOperationException", "Arrays"],
        "difficulty": "intermediate",
    },
    {
        "error_title": "Char arithmetic concatenating as a number instead of text",
        "error_category": "logic_error",
        "exception_thrown": "Logic error (no exception): prints 98 instead of a1",
        "broken_code": """public class Main {
    public static void main(String[] args) {
        char letter = 'a';
        System.out.println(letter + 1);
    }
}""",
        "conceptual_tutor_hint": "A `char` is a numeric type underneath. When you add a number to it before any text is involved, what kind of addition does Java perform? How would you force text concatenation?",
        "common_cause": "Expecting char plus int to concatenate instead of performing numeric addition.",
        "tags": ["char", "concatenation", "operators"],
        "difficulty": "beginner",
    },
]


def validate_corpus(corpus: List[Dict[str, Any]] | None = None) -> None:
    """Assert every record is complete and error titles are unique."""
    entries = corpus if corpus is not None else JAVA_ERROR_CORPUS
    required = {"error_title", "exception_thrown", "broken_code", "conceptual_tutor_hint"}
    titles: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        missing = required - entry.keys()
        if missing:
            raise ValueError(f"Corpus entry {index} is missing required keys: {sorted(missing)}")
        if entry["error_title"] in titles:
            raise ValueError(f"Duplicate error_title at entry {index}: {entry['error_title']}")
        titles.add(entry["error_title"])


validate_corpus()
