# Hostile Fixture

Markers inside fenced code should be inert:

```markdown
<!-- sync:skills -->
this is not a real marker
<!-- /sync:skills -->
```

This unclosed marker should produce a parse error:

<!-- sync:skills -->
unclosed body
