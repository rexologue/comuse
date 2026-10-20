from common.tools.guides import domain_guide


domain_guide(
    "data",
    compiler="""
Строки таблицы для шаблона готовь шагом data.map_rows: fields — выражения над row БЕЗ фигурных скобок
(например {"client": "row.key", "amount": "row.sums['Сумма'] | format_number"}), limit — сколько строк.""",
)
