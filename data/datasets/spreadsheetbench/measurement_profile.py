"""Declared workbook origin and independently captured implementation roles."""
from pathlib import Path
from data.scaffolds.spreadsheet_provenance import profile as spreadsheet_profile


def profile():
    return spreadsheet_profile(Path(__file__).resolve().parents[3], 'spreadsheetbench')
