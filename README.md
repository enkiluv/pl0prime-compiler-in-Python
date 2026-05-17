# PL/0' Compiler — Python Edition

A faithful Python translation of the PL/0' compiler originally written in Java by **Professor Ikuo Nakada** (中田育男, Waseda University).

---

## Dedication

> *To Professor Ikuo Nakada (中田育男),*
> *whose elegantly crafted compiler has guided countless students*
> *through the inner workings of language processors.*
> *This translation is offered in gratitude and admiration.*

The original (Java) source was designed as a teaching compiler accompanying Professor Nakada's textbook on compiler construction. Its remarkable clarity — a complete pipeline from lexical analysis through virtual-machine execution in fewer than 600 lines of core logic — makes it an ideal foundation for study and translation.

---

## What Is PL/0'?

PL/0' is a small structured programming language, an extension of Wirth's original PL/0. It supports:

- Named constants (`const`), variables (`var`), and nested functions (`function`)
- Assignment, `if`/`then`, `while`/`do`, `begin`/`end` blocks
- `return` with a value, `write`, `writeln`
- Recursive function calls with parameters
- Arithmetic (`+ - * /`), relational (`= <> < > <= >=`), and `odd` operators

```pl0
function fact(n)
begin
  if n = 1 then return 1;
  return n * fact(n - 1);
end;

var x;
begin
  x := 1;
  while x < 10 do
    begin
      write x;  write fact(x);  writeln;
      x := x + 1;
    end;
end.
```

---

## Compiler Pipeline

```
Source file (.pl0)
      │
      ▼
 GetSource          ← lexical analysis, error reporting, HTML output
      │  tokens
      ▼
  Compiler          ← recursive-descent parser
   │        │
   ▼        ▼
 Table    CodeGen / CodeGenB   ← symbol table  /  code generation
              │
              ▼
         execute()             ← stack-machine virtual machine
```

---

## File Structure

| File | Java equivalent | Role |
|------|----------------|------|
| `pl0.py` | `PL0.java` | Entry point; argument parsing; pipeline assembly |
| `get_source.py` | `GetSource.java` | Lexer, error handler, HTML-annotated output |
| `table.py` | `Table.java` | Symbol table |
| `code_gen.py` | `CodeGen.java` | Instruction-object code generator + stack-machine VM |
| `code_gen_b.py` | `CodeGenB.java` | Compact bytecode generator + bytecode VM |
| `compiler.py` | `Compile.java` | Recursive-descent parser / compiler |

---

## Usage

```bash
python pl0.py [options] <source.pl0>
```

| Option | Effect |
|--------|--------|
| `-s` | Print the symbol table at the end of each block |
| `-o` | List the generated object code after compilation |
| `-t` | Trace execution (print stack state before each instruction) |
| `-b` | Use the bytecode back-end (`CodeGenB`) instead of the default |

Options may be combined: `python pl0.py -sot fact.pl0`

### Output

A file `<source>.pl0.html` is always written alongside the source.  
It contains an HTML-annotated copy of the source with errors highlighted:

| Colour | Meaning |
|--------|---------|
| **Blue** | Inserted (missing) token |
| **Red** | Deleted (spurious) token |
| **Green** | Type error |

---

## Comparing the Two Back-ends

```bash
# Instruction-object code  (human-readable, one object per instruction)
python pl0.py -o fact.pl0

# Compact bytecode  (variable-length binary encoding)
python pl0.py -ob fact.pl0
```

`CodeGen` stores each instruction as a Python object — easy to read and debug.  
`CodeGenB` encodes instructions as raw bytes, mirroring the format used by real
virtual machines such as the JVM or CPython. Use them side-by-side to study the
trade-offs between representation clarity and compactness.

---

## Requirements

- Python 3.9 or later (uses `dataclasses`, `struct`, `enum.IntEnum`)
- No third-party packages required

---

## Translation Notes

This Python edition is a line-by-line faithful translation of the original Java.
Every algorithmic decision — the recursive-descent grammar, the display-based
call convention, the back-patching strategy, the HTML error-annotation scheme —
is preserved exactly as Professor Nakada designed it.

The following changes were made, each for a clearly stated reason:

### 1. Python language idioms

| Java construct | Python equivalent | Reason |
|----------------|-------------------|--------|
| `static final int` constants | `class` with plain `int` attributes, or `IntEnum` | Idiomatic Python; `IntEnum` allows arithmetic comparison without casting |
| Multiple `class` files | `@dataclass` for value types (`RelAddr`, `ConstEntry`, …) | Eliminates boilerplate constructors; fields are self-documenting |
| `System.exit(1)` | `sys.exit(1)` | Direct equivalent |
| `String.intern()` | Native Python string identity (strings are interned by default for identifiers) | No action needed |
| `char[]` for identifiers | `list` of `str`, joined with `''.join()` | Idiomatic Python |

### 2. Source-file encoding

**Change:** `open(filename, 'r', encoding='utf-8-sig')` instead of `'utf-8'`.  
**Reason:** Source files created on Windows may contain a UTF-8 BOM
(`EF BB BF`). The `'utf-8-sig'` codec strips the BOM silently; without it the
BOM is returned as the first character (U+FEFF, code-point > 255) and causes an
`IndexError` in the character-class lookup table.

### 3. Windows line endings

**Change:** `line.rstrip('\r\n')` instead of `line.rstrip('\n')`.  
**Reason:** Files saved on Windows use `\r\n` line endings. The original Java
`BufferedReader.readLine()` strips `\r` automatically; Python's `readline()` does
not, leaving a stray `\r` that maps to code-point 13 — outside the expected
character-class table range — and raises an `IndexError`.

### 4. Spurious blank line at the top of the HTML output

**Change:** `init_source()` now reads the first real source character
(`self._ch = self._next_char()`) and resets the whitespace counters to zero,
instead of leaving `self._ch = '\n'` as the initial lookahead.  
**Reason:** The synthetic `'\n'` used as an initial value caused `next_token()`
to count one phantom newline before any real source character had been read,
producing an unwanted blank line immediately after `<PRE>` in every HTML file.
The fix is transparent: the first real character is read once at initialisation
and the whitespace counters are zeroed, so the lexer loop begins with accurate
state.

### 5. Error-insertion marker position in HTML

**Change:** `error_insert()` now calls `self._flush_spaces()` before writing
the `<FONT>` tag.  
**Reason:** In the original Java (and the initial Python translation), the
inserted-token marker was written to the HTML file before the pending whitespace
(newlines and spaces) had been flushed. This placed the blue marker at the end
of the preceding line rather than at the correct position — the point of the
missing token — making errors invisible to casual inspection. Flushing the
pending whitespace first ensures the marker appears exactly where the token
should have been.

---

## Author of the Python Translation

**Myung Ho Kim**  
enkiluv@gmail.com

---

## Original Author

**Ikuo Nakada** (中田育男)  
Professor Emeritus, Waseda University  
Author of *コンパイラの構成と最適化* (Compiler Construction and Optimisation),  
Asakura Publishing — the standard Japanese textbook on compiler construction.

---

## License

The original source was published as course material accompanying Professor
Nakada's textbook. This Python translation is shared for educational purposes
under the same spirit. Please credit both Professor Nakada and this repository
if you use or adapt it in your own teaching or research.
