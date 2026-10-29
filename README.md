# AI-Powered Career Intelligence & Automated Document Tailoring Agent

An autonomous, multi-platform job scraping and resume tailoring pipeline built with Python, LLM APIs, and GitHub Actions.

## Overview
This project automates the tedious process of job hunting by:
1.  **Scraping:** Extracting fresh job postings from 20+ platforms (LinkedIn, Indeed, Startup ATS boards) using native APIs and BeautifulSoup.
2.  **Filtering:** Using smart constraints to strictly filter for entry-level/fresher Data Analytics and GenAI roles, discarding senior or irrelevant positions.
3.  **Tailoring:** Leveraging LLMs (Gemini/Llama) to dynamically tailor a base resume against each specific Job Description (JD). It guarantees a 100% humanized, ATS-friendly output without using AI buzzwords.
4.  **Generating:** Rendering strict 1-page PDF resumes using `WeasyPrint`.
5.  **Tracking:** Automatically logging application statuses, match scores, and direct PDF links into a centralized Google Sheet.

## Technical Architecture
*   **Language:** Python (Pandas, BeautifulSoup, Requests)
*   **AI Integration:** Google GenAI API, Groq API (Llama-3)
*   **Automation:** GitHub Actions (Cron jobs, CI/CD)
*   **Data Storage:** Google Sheets API
*   **Document Generation:** WeasyPrint (HTML/CSS to PDF)

## Features
*   **Multi-Platform Discovery:** Bypasses basic boards to find hidden startup roles via Greenhouse, Lever, and niche remote APIs.
*   **Smart Quota System:** Balances full-time fresher roles and internships (e.g., 60:40 ratio).
*   **Zero-Hallucination Lock:** Enforces a strict whitelist of known skills (SQL, Power BI, Python) to ensure interview safety.
*   **Automated Daily Execution:** Runs completely in the cloud via GitHub Actions.

## Setup & Usage (For demonstration purposes)
*(Add instructions here on how someone could fork this repo, add their own `base_resume.json`, set up their API keys as GitHub Secrets, and run the workflow).*
