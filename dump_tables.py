import docx
import sys

filename = "examples/raquisitos fm.docx"

try:
    doc = docx.Document(filename)
    with open("tables_dump.txt", "w", encoding="utf-8") as f:
        for t_idx, table in enumerate(doc.tables):
            f.write(f"\n--- Table {t_idx} ---\n")
            for row in table.rows:
                row_text = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                f.write(" | ".join(row_text) + "\n")
    print("Dumped tables to tables_dump.txt")

except Exception as e:
    print(f"Error: {e}")
