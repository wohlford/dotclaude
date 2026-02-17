# Claude Code Development Workflows

These proven workflows optimize Claude's performance on different types of development tasks. Choose the workflow that best matches your problem.

## Workflow A: Explore, Plan, Code, Commit

Best for: Complex problems requiring research and planning, feature additions, architectural changes.

**Steps:**

1. **Explore** - Have Claude read relevant context WITHOUT writing code yet
   - Provide general pointers: "read the file that handles logging"
   - Or specific filenames: "read logging.py"
   - Explicitly state: "don't write any code yet"
   - **Consider using subagents** for complex problems to investigate details and verify information

2. **Plan** - Ask Claude to create an implementation plan
   - Use **thinking triggers** for deeper analysis:
     - "think" - standard thinking budget
     - "think hard" - increased thinking budget
     - "think harder" - significantly increased budget
     - "ultrathink" - maximum thinking budget
   - Optional: Create a document or GitHub issue with the plan for easy reset points

3. **Code** - Ask Claude to implement the solution
   - Have Claude verify reasonableness as it implements
   - Can ask Claude to work incrementally on different pieces

4. **Commit** - Create commits and pull requests
   - Update READMEs and changelogs
   - Document what was changed and why

**Why this works:** Steps #1-#2 prevent Claude from jumping straight to coding. Research and planning significantly improve results for problems requiring deeper thinking.

**Example interaction:**
```text
You: Read the authentication module and related config files, but don't write code yet.
Claude: [reads files]
You: Think hard about how to add OAuth2 support while maintaining backward compatibility.
Claude: [creates detailed plan]
You: Looks good! Implement this plan and verify each component works.
Claude: [implements solution]
You: Please commit this and update the README with setup instructions.
```

## Workflow B: Write Tests, Commit; Code, Iterate, Commit (TDD)

Best for: Changes with clear input/output, features that are easily testable, bug fixes with reproducible cases.

**Steps:**

1. **Write tests** based on expected behavior
   - Explicitly state you're doing test-driven development
   - This prevents Claude from creating mock implementations
   - Tests should fail initially (no implementation exists yet)

2. **Commit tests** - Save the tests before implementation
   - Run tests to confirm they fail properly
   - Tell Claude NOT to write implementation code yet

3. **Implement code** - Write code to pass the tests
   - Tell Claude NOT to modify the tests
   - Claude will iterate: write → run tests → adjust → run tests
   - Continue until all tests pass
   - Optional: Have subagents verify the implementation isn't overfitting to tests

4. **Commit code** - Save the working implementation
   - All tests should pass
   - Update documentation as needed

**Why this works:** Claude performs best with clear targets to iterate against. Tests provide concrete goals that Claude can work toward incrementally, improving the code with each iteration.

**Example interaction:**
```text
You: We're doing TDD. Write tests for a function that parses ISO dates into timestamps. Don't implement the function yet.
Claude: [creates test_date_parser.py]
You: Run the tests to confirm they fail, but don't write any implementation.
Claude: [runs pytest, tests fail as expected]
You: Good! Now commit these tests.
Claude: [commits tests]
You: Now implement date_parser.py to make all tests pass. Don't modify the tests.
Claude: [implements, runs tests, adjusts, runs tests... until all pass]
You: Excellent! Commit the implementation and update the README.
```

## Workflow Selection Guide

| Scenario | Recommended Workflow | Notes |
|----------|---------------------|-------|
| New feature with unclear requirements | **A: Explore, Plan** | Use thinking triggers |
| Bug with reproducible test case | **B: TDD** | Write failing test first |
| Architecture refactoring | **A: Explore, Plan** | Heavy planning phase |
| API endpoint addition | **B: TDD** | Clear input/output |
| Performance optimization | **A: Explore, Plan** | Research current implementation |
| Integration with external service | **B: TDD** | Mock external calls in tests |
| Complex algorithm implementation | **A: Explore, Plan** | Use "think harder" or "ultrathink" |

## Best Practices

1. **Provide clear targets** - Visual mocks, test cases, or expected outputs help Claude iterate effectively
2. **Use subagents strategically** - Delegate investigation tasks to subagents early in conversations to preserve context
3. **Break into phases** - Separate research/planning from implementation to prevent premature coding
4. **Verify iteratively** - Have Claude check its work at each step rather than waiting until the end
5. **Document as you go** - Update documentation and commit messages while context is fresh
