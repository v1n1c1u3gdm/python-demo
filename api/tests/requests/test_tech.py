def test_tech_report_renders_html(client):
    response = client.get("/tech")

    assert response.status_code == 200
    assert "text/html" in response.content_type
    body = response.data.decode("utf-8")
    assert "/tech &mdash; python-demo diagnostics" in body
    assert "Licença" in body

