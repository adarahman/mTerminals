# Semantic Color System


> **Product:** mTerminals  
> **Architecture baseline:** 2026-08-07 project snapshot  
> **Status:** Authoritative design target unless marked otherwise  
> **Rule language:** SHALL = required; SHOULD = recommended; MAY = optional.


Colors communicate state:

- Positive/Bullish.
- Negative/Bearish.
- Warning/Risk.
- Informational.
- Neutral/Muted.
- Disabled.
- Focus.

## Rules

1. Bullish and profit may share a semantic family but context labels remain explicit.
2. Red/green alone SHALL not carry meaning.
3. Background tints SHOULD be restrained.
4. Heatmaps require a legend/scale and accessible numeric backup.
5. Theme tokens live centrally; component CSS does not invent arbitrary semantic colors.

## Implementation status

Positive, negative, warning, informational, disabled and neutral semantics are
owned by `styles/theme.css`. UI controls combine color with text, signs, icons or
labels; focus styling consumes the informational theme token.
