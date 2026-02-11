# AGENTS.md

## Project Context
You are helping build an AI tutor that answers CIE A Levels Computer Science questions.

## Current Scope
- Exam board: Cambridge International (CIE) A Levels
- Syllabus: 9618
- Paper: 1 (AS Level)
- Expansion to other papers/subjects/boards will happen later.

## Answer Requirements
- Output must have two parts:
  1. Exact Answer (mark-scheme style; concise, keyword-focused)
  2. Short Explanation (brief, plain-language clarification)

## Command Word Handling
- The command word (e.g., Identify, Explain, Describe) must be inferred from the question text.
- The command word determines the response depth and style.

## Style Guidance
- Favor mark-scheme keywords and precise terminology.
- Keep answers concise and aligned to AS Level expectations.

## If Ambiguous
- If the question text does not clearly include a command word, ask the user for the intended command word.
