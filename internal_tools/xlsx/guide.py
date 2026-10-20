from common.tools.guides import domain_guide


domain_guide(
    "xlsx",
    agent="Все числа из таблиц (суммы, количества, топы) считай xlsx.aggregate — сам числа не складывай.",
    compiler="Числа считай xlsx.aggregate: число строк — .output.rows, суммы — .output.totals, группы — .output.groups.",
)
