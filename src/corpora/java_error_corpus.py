"""The synthetic Java error corpus backing the code-repair retrieval tier.

Provenance
----------
These 50 patterns were **drafted by an LLM (OpenCode) on 2026-09-27**, not
hand-authored. ``README.md`` previously described this file as "50 hand-authored
patterns"; that was accurate when the file existed and is not accurate now, so
the line was corrected rather than left standing. If you edit the wording in
``README.md``, keep the two in step.

They are synthetic by design, not a scraped sample of real student errors: the
original design called for a small, curated set covering the exceptions a
first-year Java student actually meets, and nothing in the system treats this as
a measured dataset. That distinction matters when writing the paper - do not
describe these as collected data, and do not report a retrieval score measured
against them as if it generalised to real student error logs.

What a pattern is for
---------------------
Each entry is retrieved semantically when a student pastes broken Java, and the
``conceptual_tutor_hint`` is placed in the tutor's context. That makes the hint
the load-bearing field, and it is deliberately **not** the fix:

* it must not name the corrected code, because the model paraphrases this text
  and a hint that hands over the edit leaks the answer past the Scaffolding
  Engine and the Guardrail Agent;
* it must point at the concept the student is missing, so the student still does
  the diagnosing.

``validate_corpus`` enforces the mechanical half of that: required fields present,
``error_title`` unique (it is the upsert conflict target), ``tags`` a list,
``difficulty`` in range, and no hint that reads as handing over the answer.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Java is taught in Internet Programming. Patterns that are not specific to that
#: module's material are left as ``None`` below, which the retriever treats as
#: "general" and offers in every module - a student pasting Java into another
#: module's chat should still get help.
DEFAULT_MODULE_ID = "IPRT301"

LANGUAGE = "Java"

#: Allowed values for ``difficulty``.
DIFFICULTIES = ("beginner", "intermediate", "advanced")

#: Phrases that would mean a hint gives the answer away rather than leading to
#: it. Checked case-insensitively as substrings.
_LEAK_MARKERS = (
    "change this to",
    "change it to",
    "replace this with",
    "replace it with",
    "use this instead",
    "the fix is",
    "fix it by",
    "you should write",
    "you need to write",
    "correct code",
    "corrected code",
    "working code",
    "here is the fix",
    "here's the fix",
    "add this line",
    "paste this",
    "syntax should be",
    "should be ==",
)


def _p(
    error_title: str,
    error_category: str,
    exception_thrown: str | None,
    broken_code: str,
    conceptual_tutor_hint: str,
    common_cause: str,
    tags: Iterable[str],
    difficulty: str,
    module_id: str | None = None,
) -> dict[str, Any]:
    """One corpus entry. Keyword-only at the call sites above for readability."""
    return {
        "error_title": error_title,
        "error_category": error_category,
        "exception_thrown": exception_thrown,
        "broken_code": broken_code.strip("\n"),
        "conceptual_tutor_hint": conceptual_tutor_hint.strip("\n"),
        "common_cause": common_cause.strip("\n"),
        "tags": list(tags),
        "difficulty": difficulty,
        "module_id": module_id,
        "language": LANGUAGE,
    }


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------
#: Patterns are generally left unscoped (``module_id=None``) so that a student who
#: pastes Java into any module's chat still gets help. Only patterns that lean on
#: a specific module's own material are tagged with its id.
JAVA_ERROR_CORPUS: list[dict[str, Any]] = [
    # --- Null references ----------------------------------------------------
    _p(
        "NullPointerException when calling a method on an uninitialised object",
        "null-reference",
        "java.lang.NullPointerException",
        """
class Student {
    String name;
    void rename(String n) { this.name = n; }
}

Student s = new Student();
System.out.println(s.getName().length());
""",
        "An object reference can point at nothing at all while still being a "
        "perfectly valid reference to hold. Which of the two objects on this line "
        "was actually constructed, and which one is only a label pointing at an "
        "empty slot?",
        "A reference variable is created and assigned null by default; calling a "
        "method on it dereferences nothing.",
        ["null", "NullPointerException", "reference", "object", "initialisation"],
        "beginner",
    ),
    _p(
        "NullPointerException from an uninitialised array element",
        "null-reference",
        "java.lang.NullPointerException",
        """
String[] names = new String[3];
for (int i = 0; i < names.length; i++) {
    names[i] = names[i].toUpperCase();
}
""",
        "Creating an array of references does not create the objects the array "
        "points at. What does every slot hold the instant the array exists, before "
        "the loop runs?",
        "new String[3] allocates three null slots, not three empty strings.",
        ["null", "array", "NullPointerException", "initialisation", "loop"],
        "beginner",
    ),
    _p(
        "Null dereference after a method that can return nothing",
        "null-reference",
        "java.lang.NullPointerException",
        """
String line = reader.readLine();
int spaces = line.trim().length();
""",
        "One of these two lines can legitimately produce nothing, and the next one "
        "treats the result as certain. What does a read that reaches the end of "
        "input hand back, and how would you want the code to behave in that case?",
        "readLine() returns null at end of stream and the return value was used "
        "without checking.",
        ["null", "readLine", "NullPointerException", "file", "input"],
        "intermediate",
        module_id=DEFAULT_MODULE_ID,
    ),
    _p(
        "Field left null by a constructor that ignores its own parameter",
        "null-reference",
        "java.lang.NullPointerException",
        """
class Account {
    private String owner;
    private double balance;

    public Account(String owner) {
        this.balance = 0;
    }

    public String describe() {
        return owner + " owes " + balance;
    }
}
""",
        "This constructor takes an owner as a parameter and then only uses one of "
        "its parameters. What is the value of the field that was never assigned, "
        "and where in the class would you expect every field to be given a value?",
        "A constructor that does not assign every field leaves that field null.",
        ["constructor", "field", "null", "NullPointerException", "initialisation"],
        "beginner",
    ),
    _p(
        "Optional.get() throws NoSuchElementException on an empty Optional",
        "null-reference",
        "java.util.NoSuchElementException",
        """
Optional<String> nickname = lookupNickname(id);
String value = nickname.get();
""",
        "This container is explicitly designed to represent the possibility of "
        "there being no value at all. What does the method that insists on a value "
        "do when there is none - and which other method on the same object would "
        "let the program carry on?",
        "Optional.get() throws if empty; orElse/orElseGet handle absence.",
        ["Optional", "NoSuchElementException", "null", "stream", "api"],
        "intermediate",
    ),

    # --- Off-by-one and loop bounds -----------------------------------------
    _p(
        "ArrayIndexOutOfBoundsException from <= in a loop condition",
        "index-bounds",
        "java.lang.ArrayIndexOutOfBoundsException",
        """
int[] numbers = { 2, 4, 6 };
for (int i = 0; i <= numbers.length; i++) {
    System.out.println(numbers[i]);
}
""",
        "The loop and the array disagree about where a valid index stops. How many "
        "positions does an array of this size actually have, and which of those two "
        "numbers is one past the last one?",
        "<= runs the body with i == length, one past the final valid index.",
        ["array", "loop", "index", "ArrayIndexOutOfBoundsException", "off-by-one"],
        "beginner",
    ),
    _p(
        "StringIndexOutOfBoundsException from lastIndexOf plus one",
        "index-bounds",
        "java.lang.StringIndexOutOfBoundsException",
        """
String word = "inheritance";
int cut = word.lastIndexOf("a") + 1;
String tail = word.substring(cut);
""",
        "This pattern searches for a character and then steps one past where it was "
        "found. If the search finds nothing, what number does it return, and what "
        "does adding one to that produce?",
        "lastIndexOf returns -1 when absent, so +1 gives 0 and substring then "
        "throws instead of returning the whole string.",
        ["string", "substring", "lastIndexOf", "off-by-one", "StringIndexOutOfBounds"],
        "intermediate",
    ),
    _p(
        "Loop that never terminates because the counter is never changed",
        "loop-logic",
        None,
        """
int total = 0;
int i = 0;
while (total < 100) {
    total = total + 5;
    System.out.println(i);
}
""",
        "The condition does eventually become false, so this loop is not truly "
        "infinite - but it repeats far more times than it needs to. What variable "
        "is the loop condition actually watching, and which variable keeps being "
        "printed without ever being updated?",
        "The condition tracks a value the loop body updates, while the variable "
        "being printed stays constant.",
        ["loop", "while", "infinite", "counter", "logic"],
        "beginner",
    ),

    # --- Comparison and equality --------------------------------------------
    _p(
        "String comparison with == instead of equals",
        "comparison",
        None,
        """
String a = new String("resk");
String b = new String("resk");
if (a == b) {
    System.out.println("same");
}
""",
        "These two variables hold the same characters, yet this test can still be "
        "false. What does the constructor call at the top of this snippet actually "
        "do - hand back the one spelling, or build a second, separate thing?",
        "== compares references; two separately constructed strings are different "
        "objects holding equal characters.",
        ["string", "equals", "comparison", "reference", "identity"],
        "beginner",
    ),
    _p(
        "Wrapper class compared with == caches only part of the range",
        "comparison",
        None,
        """
Integer a = 128;
Integer b = 128;
if (a == b) {
    System.out.println("cached");
}
""",
        "This same test behaves differently if you change the number to a small "
        "one. What special range of small numbers does this wrapper class keep "
        "ready-made, and what happens once you step outside it?",
        "Integer caches -128..127, so == is true inside that range and false "
        "outside it.",
        ["wrapper", "Integer", "cache", "comparison", "boxing"],
        "advanced",
    ),
    _p(
        "char compared against a String literal",
        "comparison",
        None,
        """
char grade = 'A';
if (grade == "A") {
    System.out.println("excellent");
}
""",
        "One side of this test is a single character and the other is a short piece "
        "of text. How does the language decide that two things are the same type "
        "when one of them holds a single character and the other holds a sequence?",
        "char is a primitive and String an object; they are never ==.",
        ["char", "String", "comparison", "primitive", "type"],
        "beginner",
    ),

    # --- Types and casting ---------------------------------------------------
    _p(
        "ClassCastException assigning Object to a String reference",
        "type-casting",
        "java.lang.ClassCastException",
        """
Object value = "Socratic";
String text = (String) value;
""",
        "This pair of lines works until the variable above is given something else. "
        "What does a reference of the widest possible type guarantee about what it "
        "is actually pointing at?",
        "Casting Object to a subtype is checked at runtime and fails unless the "
        "object really is that type.",
        ["cast", "Object", "ClassCastException", "type", "downcast"],
        "intermediate",
    ),
    _p(
        "ClassCastException from an unchecked cast on collection elements",
        "type-casting",
        "java.lang.ClassCastException",
        """
List<Object> items = new ArrayList<>();
items.add(42);
String label = (String) items.get(0);
""",
        "The compiler allowed this line without complaint, which is the clue. What "
        "did the compiler have no way of knowing at that point, and where is the "
        "check that ends up failing instead?",
        "Generics are erased, so the cast is only validated when the element is "
        "fetched at runtime.",
        ["generics", "erasure", "cast", "ArrayList", "ClassCastException"],
        "advanced",
    ),
    _p(
        "NumberFormatException from parsing text that is not a number",
        "type-casting",
        "java.lang.NumberFormatException",
        """
String input = "12 apples";
int count = Integer.parseInt(input);
""",
        "This method takes text and promises to give back a number, so it has to "
        "decide what to do when the text is something else entirely. What would you "
        "want a program to do at the point where the text turns out not to be a "
        "number: stop, or carry on with a value?",
        "parseInt throws when the string is not a valid integer.",
        ["parseInt", "NumberFormatException", "string", "input", "validation"],
        "beginner",
    ),

    # --- Arithmetic ----------------------------------------------------------
    _p(
        "ArithmeticException from integer division by zero",
        "arithmetic",
        "java.lang.ArithmeticException",
        """
int rows = 0;
int perRow = 8;
int columns = rows / perRow;
""",
        "One of these two numbers was meant to be configurable and the other is a "
        "fixed size. Which of them would a user be able to set to zero, and what "
        "should the program do if they do?",
        "Integer division by zero throws; there is no integer result to return.",
        ["division", "zero", "ArithmeticException", "arithmetic", "validation"],
        "beginner",
    ),
    _p(
        "ArithmeticException from bigDecimal division by a zero-valued divisor",
        "arithmetic",
        "java.lang.ArithmeticException",
        """
BigDecimal taxRate = new BigDecimal("0.00");
BigDecimal total = new BigDecimal("100.00");
BigDecimal tax = total.divide(taxRate);
""",
        "The same idea as dividing by zero with whole numbers, reached through a "
        "decimal type instead. Does using a type built specifically for exact "
        "fractions change the rule about dividing by zero, or only the precision?",
        "BigDecimal.divide throws ArithmeticException on a zero divisor just as "
        "integer division does.",
        ["BigDecimal", "division", "zero", "ArithmeticException"],
        "intermediate",
    ),

    # --- Collections ---------------------------------------------------------
    _p(
        "ConcurrentModificationException from modifying a list while iterating",
        "collections",
        "java.util.ConcurrentModificationException",
        """
List<String> modules = new ArrayList<>(List.of("IPRT", "PBDV"));
for (String m : modules) {
    if (m.equals("PBDV")) {
        modules.remove(m);
    }
}
""",
        "The thing being removed was found by the very loop that is doing the "
        "removing. What does the loop hold onto while it walks the collection, and "
        "what happens to that position count when an element disappears?",
        "The iterator tracks a modification count; structural changes mid-loop trip "
        "its check.",
        ["iterator", "ArrayList", "ConcurrentModificationException", "remove", "for-each"],
        "intermediate",
    ),
    _p(
        "IndexOutOfBoundsException from ArrayList.get with a fixed index",
        "collections",
        "java.lang.IndexOutOfBoundsException",
        """
List<String> modules = new ArrayList<>();
modules.add("IPRT");
modules.add("PBDV");
String first = modules.get(2);
""",
        "This collection grew by one element on each of the two lines above, and "
        "then something asked for a position beyond that. Counting from zero, what "
        "is the highest position this list actually has?",
        "A two-element list has valid indexes 0 and 1 only.",
        ["ArrayList", "get", "index", "IndexOutOfBoundsException", "zero-based"],
        "beginner",
    ),
    _p(
        "String built by concatenation inside a large loop",
        "performance",
        None,
        """
String result = "";
for (int i = 0; i < 100000; i++) {
    result = result + i;
}
""",
        "The loop runs a hundred thousand times and each pass appears to do a small "
        "amount of work. Why does the total time grow much faster than the number "
        "of passes, and which type in the language is built specifically for "
        "building a long piece of text in stages?",
        "Strings are immutable, so each + copies everything built so far, giving "
        "quadratic total copying.",
        ["String", "concatenation", "performance", "StringBuilder", "immutable"],
        "intermediate",
    ),
    _p(
        "Removing from an ArrayList by index inside a loop skips elements",
        "collections",
        None,
        """
List<String> items = new ArrayList<>(List.of("a", "b", "c", "d"));
for (int i = 0; i < items.size(); i++) {
    if (items.get(i).equals("b")) {
        items.remove(i);
    }
}
""",
        "Nothing throws here, which is what makes it confusing: the result is "
        "simply not what was intended. After the removal, every later element has "
        "moved down one position - so what does the counter compare itself against "
        "on the very next pass?",
        "remove(int) shifts later elements left, so the index-based loop re-reads "
        "and skips one.",
        ["ArrayList", "remove", "index", "loop", "skip"],
        "intermediate",
    ),

    # --- Objects, inheritance, interfaces ------------------------------------
    _p(
        "Abstract class cannot be instantiated directly",
        "object-oriented",
        "java.lang.InstantiationException",
        """
abstract class Shape {
    abstract double area();
}

Shape s = new Shape();
""",
        "This class was given no body for its area calculation on purpose. What does "
        "it mean for a class to be declared without a complete implementation, and "
        "so what would a half-built object of that class even be able to do?",
        "An abstract class exists to be extended, never instantiated directly.",
        ["abstract", "InstantiationException", "inheritance", "polymorphism"],
        "beginner",
    ),
    _p(
        "Interface method not implemented at compile time",
        "object-oriented",
        None,
        """
interface Drawable {
    void draw();
}

class Canvas implements Drawable {
    public void paint() {
        System.out.println("painting");
    }
}
""",
        "The class promised to implement a contract, and then named its method "
        "something else. How does the language decide which methods a class has to "
        "provide once it agrees to a contract, and what would a caller who holds "
        "only the contract type be unable to do?",
        "Every method in the interface must be implemented with the exact signature "
        "or the class must be abstract.",
        ["interface", "implements", "abstract", "signature", "compile"],
        "beginner",
    ),
    _p(
        "Superclass field hidden by a same-named subclass field",
        "object-oriented",
        None,
        """
class Employee {
    protected double salary = 3000;
}

class PartTime extends Employee {
    protected double salary = 1800;
}

public class Main {
    public static void main(String[] args) {
        PartTime p = new PartTime();
        System.out.println(p.salary);
    }
}
""",
        "Both classes have a member with the same name, and only one value came "
        "out. When a subclass declares a member that its parent already has, whose "
        "member does the name refer to, and how would you explicitly reach the one "
        "that got hidden?",
        "A subclass field shadows the inherited one; super.field reaches the parent.",
        ["inheritance", "field", "shadowing", "super", "superclass"],
        "intermediate",
        module_id=DEFAULT_MODULE_ID,
    ),
    _p(
        "Static method called on an instance hides the real behaviour",
        "object-oriented",
        None,
        """
class Counter {
    static int count = 0;
    static void increment() { count++; }
}

Counter c = new Counter();
c.increment();
System.out.println(Counter.count);
""",
        "This member belongs to the class rather than to any one object, and that "
        "is why the value survives. If two different objects were created, how many "
        "copies of that value would exist, and what does that imply about where the "
        "variable is actually held?",
        "Static state is per class, not per instance, so it is shared.",
        ["static", "field", "instance", "class", "shared state"],
        "intermediate",
    ),
    _p(
        "Superclass method runs instead of the override when the parent type is used",
        "object-oriented",
        None,
        """
class Animal {
    public String speak() { return "..."; }
}

class Dog extends Animal {
    public String speak() { return "woof"; }
}

Animal a = new Dog();
System.out.println(a.speak());
""",
        "Two classes declare a method with this name and only one of them is the "
        "one being called. What does the type written on the variable decide, and "
        "what decides it instead?",
        "A static-typed call uses the declared type unless the subclass method "
        "carries @Override, which makes the compiler check it really overrides.",
        ["override", "polymorphism", "inheritance", "annotation", "static typing"],
        "intermediate",
        module_id=DEFAULT_MODULE_ID,
    ),
    _p(
        "Anonymous inner class captures a local variable that later changes",
        "object-oriented",
        None,
        """
int bonus = 10;
Runnable task = new Runnable() {
    public void run() {
        bonus = bonus + 5;
        System.out.println(bonus);
    }
};
task.run();
""",
        "The anonymous class reads a variable declared outside it, and it wants to "
        "change it. What restriction does the language place on a local variable "
        "that an inner class touches - and what would that variable have to be for "
        "this to be allowed?",
        "Local variables captured by an inner class must be final or effectively "
        "final.",
        ["inner class", "anonymous", "final", "effectively final", "capture"],
        "advanced",
    ),

    # --- Constructors, initialisers, scope -----------------------------------
    _p(
        "Local variable used before it has been given a value",
        "scope",
        "java.lang.IllegalStateException",
        """
int total;
total = total + 5;
System.out.println(total);
""",
        "The variable exists as a name from the moment it is declared, which is "
        "before the line that is meant to give it a starting point. What value does "
        "a local variable of a numeric type hold in that gap?",
        "Locals have no default value; they must be definitely assigned before use.",
        ["variable", "initialisation", "assignment", "scope", "compile"],
        "beginner",
    ),
    _p(
        "Field initialiser read by a method the constructor calls",
        "scope",
        None,
        """
class Config {
    int retries = 3;
    Config() {
        this.retries = calculateRetries() - 1;
    }
    int calculateRetries() { return this.retries; }
}
""",
        "One of these assignments asks a method for a value, and that method reads "
        "a field which another line has already set. In what order does the language "
        "actually run field initialisers, constructor bodies and methods on this "
        "object?",
        "Field initialisers run in declaration order before the constructor body.",
        ["field", "initialiser", "constructor", "order", "lifecycle"],
        "advanced",
    ),
    _p(
        "Primitive parameter reassigned inside the method, caller unaffected",
        "scope",
        None,
        """
static void grow(int size) {
    size = size * 2;
}

int width = 10;
grow(width);
System.out.println(width);
""",
        "The method clearly changed its parameter, and the value that was printed "
        "did not move. What is the relationship between the value a caller holds and "
        "the name a method receives when the type is a primitive?",
        "Java passes primitives by value, so reassigning the parameter cannot "
        "affect the caller's variable.",
        ["parameter", "pass by value", "primitive", "scope", "method"],
        "intermediate",
    ),

    # --- Input, files, exceptions --------------------------------------------
    _p(
        "FileNotFoundException because the path is relative to the working directory",
        "io",
        "java.io.FileNotFoundException",
        """
File file = new File("data/students.txt");
if (!file.exists()) {
    System.out.println("no file at: " + file.getAbsolutePath());
}
BufferedReader reader = new BufferedReader(new FileReader(file));
""",
        "The path here is not absolute, so what it points at depends on something "
        "outside the program. Which directory is a relative path resolved against, "
        "and would that be the same directory every time the program is launched?",
        "Relative paths resolve against the process working directory, which varies "
        "by how the program was started.",
        ["FileNotFoundException", "File", "path", "io", "working directory"],
        "beginner",
    ),
    _p(
        "FileNotFoundException because the directory or filename does not exist",
        "io",
        "java.io.FileNotFoundException",
        """
File file = new File("studentmarks.csv");
if (!file.exists()) {
    System.out.println("no file: " + file.getAbsolutePath());
}
""",
        "The program asked the filesystem for something that is not there. Before "
        "opening a file, what does the filesystem have to be able to answer, and is "
        "that something the code checked first?",
        "A path was constructed but never checked, and the file does not exist at "
        "that location.",
        ["FileNotFoundException", "File", "exists", "io", "path"],
        "beginner",
    ),
    _p(
        "InputStream closed then reused",
        "io",
        "java.io.IOException: Stream closed",
        """
InputStream in = new FileInputStream("notes.txt");
String first = new String(in.readAllBytes());
String second = new String(in.readAllBytes());
""",
        "A stream is a one-way journey through data, and something happened to it "
        "between the two reads. What does the first read leave the stream in, and "
        "what does asking a stream that has ended do?",
        "A stream is consumed and closed once exhausted; it cannot be rewound.",
        ["stream", "InputStream", "io", "closed", "IOException"],
        "intermediate",
    ),
    _p(
        "IOException from a resource never closed on an early return",
        "io",
        "java.io.IOException",
        """
BufferedReader reader = new BufferedReader(new FileReader("notes.txt"));
String line = reader.readLine();
if (line == null) {
    return null;
}
return line.trim();
""",
        "There is a path through this method that leaves before the last line runs. "
        "Who is responsible for releasing the underlying file handle on each of the "
        "possible exits, and what language feature exists so that this is handled "
        "the same way on every one of them?",
        "The stream is not closed on the early-return path, leaking the handle; "
        "try-with-resources fixes it.",
        ["resource", "close", "try-with-resources", "io", "leak"],
        "intermediate",
    ),
    _p(
        "SQLException from a connection closed before the query ran",
        "io",
        "java.sql.SQLException: No operations allowed after connection closed",
        """
Connection conn = DriverManager.getConnection(url, user, pass);
conn.close();
Statement st = conn.createStatement();
ResultSet rs = st.executeQuery("SELECT 1");
""",
        "The lines are in the order they execute, and one of them makes everything "
        "after it impossible. What state has the connection been put into by the "
        "time the query is attempted?",
        "The connection was closed before the statement was created.",
        ["SQLException", "jdbc", "connection", "close", "order"],
        "intermediate",
    ),
    _p(
        "Exception swallowed by a catch block that does nothing",
        "error-handling",
        None,
        """
try {
    saveResult();
} catch (Exception e) {
}
System.out.println("saved");
""",
        "The program claims the save happened whether or not it did. What "
        "information has been thrown away by this catch block, and what could a "
        "reader of this code never find out about a failure?",
        "A silent catch discards the exception, hiding the failure entirely.",
        ["exception", "catch", "swallow", "error handling", "logging"],
        "beginner",
    ),
    _p(
        "Finally block discards the exception it was meant to preserve",
        "error-handling",
        None,
        """
try {
    parse(input);
} finally {
    System.out.println("done");
}
""",
        "This block is described as always running, whatever happened before it. If "
        "it only prints, what happens to an exception that was raised inside the "
        "try - does printing afterwards change the outcome?",
        "A finally block that completes normally lets the original exception "
        "continue; one that throws replaces it.",
        ["finally", "exception", "error handling", "propagate"],
        "advanced",
    ),

    # --- Maps, generics, streams --------------------------------------------
    _p(
        "NullPointerException on a Map lookup for a key that is not present",
        "collections",
        "java.lang.NullPointerException",
        """
Map<String, Integer> marks = new HashMap<>();
int score = marks.get("IPRT301") + 5;
""",
        "A lookup on a map returns a value only when the key is there, and this code "
        "treats the result as though it always is. What does a map return for a key "
        "it has never seen, and what would adding that missing value to a number "
        "amount to?",
        "Map.get returns null for an absent key, and null + int unboxes to NPE.",
        ["Map", "HashMap", "get", "null", "unboxing"],
        "intermediate",
    ),
    _p(
        "MissingKeyException from Map.get on an immutable map",
        "collections",
        "java.util.NoSuchElementException",
        """
Map<String, Integer> marks = Map.of("IPRT301", 80);
int score = marks.get("PBDV301");
""",
        "These two kinds of map behave differently when a key is missing, and this "
        "one is the stricter of the two. What does it do instead of quietly handing "
        "back nothing, and how would you ask it the same question in the tolerant "
        "way?",
        "Map.of rejects lookups of absent keys; containsKey or getOrDefault is the "
        "tolerant form.",
        ["Map", "Map.of", "immutable", "NoSuchElementException", "get"],
        "advanced",
    ),
    _p(
        "Raw type erases the compiler's ability to check element types",
        "generics",
        None,
        """
List values = new ArrayList();
values.add("IPRT301");
Integer code = values.get(0);
""",
        "This collection was declared without saying what it holds, and that "
        "decision moved a check the compiler could have made to somewhere much "
        "later. Which part of a generic type does the compiler throw away at "
        "runtime, and what does that mean about the line that assigns to an Integer?",
        "Raw types erase the type argument, so the bad assignment compiles and "
        "fails later at runtime.",
        ["generics", "raw type", "erasure", "ArrayList", "type safety"],
        "advanced",
    ),
    _p(
        "Stream consumed twice so the second operation finds nothing",
        "streams",
        "java.lang.IllegalStateException: stream has already been operated upon",
        """
List<String> modules = List.of("IPRT", "PBDV");
long count = modules.stream().filter(m -> m.length() > 3).count();
long again = modules.stream().count();
""",
        "Two separate chains were built from the same collection, and only the first "
        "one worked. What kind of thing does a stream represent, and why can it not "
        "be walked twice?",
        "A stream is a one-use pipeline; it is consumed by the terminal operation.",
        ["stream", "terminal operation", "one-use", "IllegalStateException"],
        "advanced",
    ),
    _p(
        "collect to a Map loses entries when two keys collide",
        "streams",
        "java.lang.IllegalStateException: Duplicate key",
        """
Map<String, Integer> lengths = modules.stream()
    .collect(Collectors.toMap(m -> m.substring(0, 2), m -> m.length()));
""",
        "Two different module names can produce the same short key. When two inputs "
        "map to one key, which one does the collector have no way of choosing "
        "between, and what would it need from you to make that choice?",
        "toMap throws on duplicate keys unless a merge function is supplied.",
        ["stream", "collect", "toMap", "duplicate key", "merge"],
        "advanced",
    ),

    # --- Overloading, arrays, misc ------------------------------------------
    _p(
        "Overload resolution picks the widening method instead of the exact match",
        "overloading",
        None,
        """
static void process(int n) { System.out.println("int " + n); }
static void process(long n) { System.out.println("long " + n); }

process(5);
""",
        "There are two methods with this name and one call picks between them. What "
        "rule does the language use to choose, and does widening an int to a long "
        "come before or after the exact match?",
        "Java picks the most specific applicable overload; the exact match beats "
        "the widening one.",
        ["overload", "int", "long", "widening", "resolution"],
        "intermediate",
    ),
    _p(
        "Object array allocated to a size but never filled",
        "arrays",
        "java.lang.NullPointerException",
        """
Scanner[] scanners = new Scanner[3];
for (Scanner s : scanners) {
    s.close();
}
""",
        "An array of references was given a size, and the loop walked all of it "
        "without trouble. What is in each of those three positions right after the "
        "array is created?",
        "new Scanner[3] allocates null slots; the objects are never created.",
        ["array", "null", "Scanner", "initialisation", "loop"],
        "beginner",
    ),
    _p(
        "Floating point equality compared exactly",
        "arithmetic",
        None,
        """
double total = 0.0;
for (int i = 0; i < 10; i++) { total += 0.1; }
if (total == 1.0) {
    System.out.println("exactly one");
}
""",
        "Adding the same fraction ten times does not land exactly on one in this "
        "type. Why is a repeated fraction impossible to store precisely, and what "
        "is the standard way to ask whether two such values are close enough to "
        "treat as equal?",
        "Binary floating point cannot represent 0.1 exactly, so the error "
        "accumulates; compare with a tolerance instead.",
        ["floating point", "double", "precision", "tolerance", "equality"],
        "intermediate",
    ),
    _p(
        "Integer overflow silently wrapping round",
        "arithmetic",
        None,
        """
int max = Integer.MAX_VALUE;
int next = max + 1;
System.out.println("next is " + next);
""",
        "Adding one to the largest value this type can hold did not produce a larger "
        "number, and nothing was reported. What does the hardware do with the extra "
        "bit, and what type exists for when you genuinely need to go further?",
        "Java wraps int arithmetic on overflow; long and BigInteger do not wrap at "
        "int's limit.",
        ["overflow", "Integer", "MAX_VALUE", "long", "BigInteger"],
        "intermediate",
    ),
    _p(
        "Adding to a list inside a method changes the caller's list",
        "collections",
        None,
        """
static void addItem(List<String> list) {
    list.add("RESK301");
}

List<String> chosen = new ArrayList<>();
addItem(chosen);
System.out.println(chosen.size());
""",
        "This looks like the mirror image of the primitive case, where changing a "
        "parameter had no effect on the caller. Here the caller's list did change - "
        "what is different about what a reference parameter actually carries?",
        "A reference parameter passes the reference itself, so mutations through it "
        "are visible to the caller.",
        ["pass by reference", "parameter", "mutation", "ArrayList", "side effect"],
        "intermediate",
    ),
    _p(
        "equals overridden without hashCode",
        "object-oriented",
        None,
        """
class Module {
    String id;
    Module(String id) { this.id = id; }

    @Override
    public boolean equals(Object o) {
        return o instanceof Module m && m.id.equals(this.id);
    }
}

Set<Module> seen = new HashSet<>();
seen.add(new Module("IPRT301"));
System.out.println(seen.contains(new Module("IPRT301")));
""",
        "Two objects were declared equal, yet the set could not find one inside the "
        "other. A hash-based collection does not just ask whether objects are equal "
        "- where does it put them, and what happens when two objects agree on one "
        "question but not the other?",
        "hashCode must be consistent with equals; overriding only equals breaks "
        "hash-based lookups.",
        ["equals", "hashCode", "HashSet", "contract", "contains"],
        "advanced",
        module_id=DEFAULT_MODULE_ID,
    ),
    _p(
        "toString overridden but string concatenation ignores it",
        "object-oriented",
        None,
        """
class Student {
    String name;
    Student(String name) { this.name = name; }

    @Override
    public String toString() { return "Student(" + name + ")"; }
}

Student s = new Student("Thandi");
String message = "Result: " + s;
System.out.println(message);
""",
        "This class describes how it should appear as text, and the output does not "
        "match. Which is being used when one operand is a String, and what would the "
        "compiler have to know to prefer the other?",
        "If either operand is a String the compiler uses concatenation; toString is "
        "only chosen for a lone operand.",
        ["toString", "concatenation", "String", "override", "compile"],
        "intermediate",
    ),
    _p(
        "Thread-safe method needed but a shared counter is not atomic",
        "concurrency",
        None,
        """
class Counter {
    private int count = 0;

    void increment() {
        count = count + 1;
    }
    int get() { return count; }
}
""",
        "Read, add, write - three steps, and two threads can sit between them. What "
        "could the final value be if both threads run this at once, and what does "
        "the language offer to make the whole sequence indivisible?",
        "count++ is not atomic; synchronised, AtomicInteger, or a lock serialises "
        "it.",
        ["concurrency", "thread", "atomic", "race condition", "synchronised"],
        "advanced",
    ),
    _p(
        "Resource named the same as a field so the field is used by mistake",
        "scope",
        None,
        """
class Report {
    private StringBuilder body = new StringBuilder();

    void add(String part) {
        body.append(part);
    }

    void reset() {
        StringBuilder body = new StringBuilder();
        body.append("cleared");
    }
}
""",
        "A local name was reused inside a method that has a field of the same name. "
        "Which of the two does a bare name inside this method refer to, and where "
        "would the value written to it have gone?",
        "The local declaration shadows the field, so the field is left untouched.",
        ["shadowing", "field", "local", "scope", "StringBuilder"],
        "intermediate",
        module_id=DEFAULT_MODULE_ID,
    ),
    _p(
        "Enhanced for loop over a collection that it then modifies",
        "collections",
        "java.util.ConcurrentModificationException",
        """
Set<String> completed = new HashSet<>();
for (String module : completed) {
    completed.add(module + "-done");
}
""",
        "This loop takes each element and immediately changes the structure of the "
        "thing being walked. How does a for-each loop actually work underneath, and "
        "what does it notice about the collection changing size while it holds a "
        "position in it?",
        "for-each creates an iterator whose modification check trips on structural "
        "change.",
        ["for-each", "iterator", "HashSet", "ConcurrentModificationException"],
        "intermediate",
    ),
]


def validate_corpus(corpus: list[dict[str, Any]] | None = None) -> None:
    """Fail loudly on a malformed corpus, before anything is embedded.

    Called by ``src.ingest_code_patterns`` so a mistake here surfaces as a clear
    error at ingest time rather than as a bad retrieval result later.
    """
    entries = JAVA_ERROR_CORPUS if corpus is None else corpus
    if not entries:
        raise ValueError("Java error corpus is empty")

    seen: set[str] = set()
    for index, pattern in enumerate(entries):
        where = f"entry {index}"
        title = pattern.get("error_title")
        if not title:
            raise ValueError(f"{where}: missing error_title")
        if title in seen:
            # error_title is the upsert conflict target, so a duplicate silently
            # overwrites the earlier pattern instead of adding a second one.
            raise ValueError(f"{where}: duplicate error_title {title!r}")
        seen.add(title)
        if len(title) > 255:
            raise ValueError(f"{where}: error_title exceeds 255 characters")

        for required in ("broken_code", "conceptual_tutor_hint"):
            if not (pattern.get(required) or "").strip():
                raise ValueError(f"{where} ({title}): missing {required}")

        if not pattern.get("language"):
            raise ValueError(f"{where} ({title}): missing language")

        tags = pattern.get("tags")
        if not isinstance(tags, list) or not tags:
            raise ValueError(f"{where} ({title}): tags must be a non-empty list")

        difficulty = pattern.get("difficulty")
        if difficulty not in DIFFICULTIES:
            raise ValueError(
                f"{where} ({title}): difficulty {difficulty!r} not in {DIFFICULTIES}"
            )

        module_id = pattern.get("module_id")
        if module_id is not None and not isinstance(module_id, str):
            raise ValueError(f"{where} ({title}): module_id must be a string or None")

        # The hint is the only part of a pattern the tutor model ever sees, so a
        # hint that states the fix collapses the scaffolding for this pattern even
        # though the Guardrail Agent will not object to it.
        hint = pattern["conceptual_tutor_hint"].casefold()
        for marker in _LEAK_MARKERS:
            if marker in hint:
                raise ValueError(
                    f"{where} ({title}): conceptual_tutor_hint reads as handing over "
                    f"the answer (matched {marker!r})"
                )


__all__ = [
    "DEFAULT_MODULE_ID",
    "DIFFICULTIES",
    "JAVA_ERROR_CORPUS",
    "LANGUAGE",
    "validate_corpus",
]
