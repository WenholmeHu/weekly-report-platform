"""钉钉表结构查询工具测试。"""

from tools import list_dingtalk_tables


def test_tools_list_dingtalk_tables_extracts_tables_and_fields(monkeypatch) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_run_mcporter(mcp_url: str, args: list[str]) -> dict[str, object]:
        calls.append((mcp_url, args))
        return {
            "data": {
                "tables": [
                    {
                        "id": "tbl001",
                        "name": "项目周报",
                        "fields": [
                            {"id": "fld001", "name": "项目名称", "type": "text"},
                            {"fieldId": "fld002", "fieldName": "风险等级", "type": "singleSelect"},
                        ],
                    },
                    {
                        "tableId": "tbl002",
                        "tableName": "项目风险",
                        "fields": [{"id": "fld003", "name": "责任人"}],
                    },
                ]
            }
        }

    monkeypatch.setattr(list_dingtalk_tables, "run_mcporter", fake_run_mcporter)

    result = list_dingtalk_tables.fetch_tables_with_fields(
        base_id="base001",
        mcp_url="https://example.com/mcp",
    )

    assert calls == [
        (
            "https://example.com/mcp",
            ["get_tables", "--args", '{"baseId": "base001"}'],
        )
    ]
    assert result == {
        "baseId": "base001",
        "tableCount": 2,
        "tables": [
            {
                "tableId": "tbl001",
                "tableName": "项目周报",
                "fields": [
                    {"fieldId": "fld001", "fieldName": "项目名称", "type": "text"},
                    {"fieldId": "fld002", "fieldName": "风险等级", "type": "singleSelect"},
                ],
            },
            {
                "tableId": "tbl002",
                "tableName": "项目风险",
                "fields": [{"fieldId": "fld003", "fieldName": "责任人", "type": ""}],
            },
        ],
    }
