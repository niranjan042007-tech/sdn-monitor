import server.monitoring_server as monitoring_server


def reset_alert_state(monkeypatch):
    messages = []
    monkeypatch.setattr(monitoring_server, "log_msg", messages.append)
    monkeypatch.setattr(monitoring_server, "last_vm_cpu_alert", 0.0)
    monkeypatch.setattr(monitoring_server, "last_vm_mem_alert", 0.0)
    return messages


def test_cpu_alert_is_shared_across_agent_reports(monkeypatch):
    messages = reset_alert_state(monkeypatch)

    # Different agents report the same shared VM metric.
    monitoring_server.check_vm_resource_alerts(80.0, 40.0, 1000.0)
    monitoring_server.check_vm_resource_alerts(82.0, 40.0, 1001.0)

    assert len(messages) == 1
    assert "high CPU on mininet-vm" in messages[0]


def test_cpu_and_memory_have_independent_cooldowns(monkeypatch):
    messages = reset_alert_state(monkeypatch)

    monitoring_server.check_vm_resource_alerts(40.0, 90.0, 1000.0)
    monitoring_server.check_vm_resource_alerts(80.0, 90.0, 1001.0)

    assert len(messages) == 2
    assert any("high CPU on mininet-vm" in msg for msg in messages)
    assert any("high MEM on mininet-vm" in msg for msg in messages)


def test_alert_repeats_after_cooldown_expires(monkeypatch):
    messages = reset_alert_state(monkeypatch)

    monitoring_server.check_vm_resource_alerts(80.0, 40.0, 1000.0)
    monitoring_server.check_vm_resource_alerts(80.0, 40.0, 1010.0)
    assert len(messages) == 1  # Exactly 10 seconds does not exceed cooldown.

    monitoring_server.check_vm_resource_alerts(80.0, 40.0, 1010.01)
    assert len(messages) == 2
