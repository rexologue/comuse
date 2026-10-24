from common.tools.guides import domain_guide


domain_guide(
    "data",
    compiler="""
Строки, которые следующему шагу нужны с другими полями (таблица шаблона), готовь шагом data.map_rows: fields —
выражения над row БЕЗ фигурных скобок, поля row — из output-схемы шага, который дал строки
(например {"client": "row.name", "amount": "row.total | format_number"}), limit — сколько строк.""",
)
