#!/bin/bash

# Static Analysis Script for Dead Code Detection

# Install required tools
pip install vulture pylint flake8

# Run Vulture for dead code detection
echo "Running Vulture static analysis..."
vulture . --min-confidence 80

# Run Pylint for code quality checks
echo "Running Pylint..."
pylint ./

# Run Flake8 for style guide violations
echo "Running Flake8..."
flake8 .

# Generate combined report
echo "Generating combined analysis report..."
echo "Vulture Results:" > analysis_report.txt
vulture . --min-confidence 80 >> analysis_report.txt

echo "Pylint Results:" >> analysis_report.txt
pylint ./ >> analysis_report.txt

echo "Flake8 Results:" >> analysis_report.txt
flake8 . >> analysis_report.txt

# Display summary
wc -l analysis_report.txt
echo "Analysis complete. Check analysis_report.txt for full details."