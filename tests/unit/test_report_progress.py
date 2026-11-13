from app.utils.report_progress import status_for_rules

REQUIRED = [
    "report",
    "diagnosis",
    "movement",
    "commercial",
    "return",
    "verification",
    "airworthiness",
]
STATUSES = [
    {"code": "reported", "sort_order": 1, "is_initial": True, "is_final": False, "advance_rules": None},
    {"code": "in_review", "sort_order": 2, "is_initial": False, "is_final": False, "advance_rules": None},
    {
        "code": "grounded",
        "sort_order": 3,
        "is_initial": False,
        "is_final": False,
        "advance_rules": [
            {"when": "step", "step_code": "diagnosis", "field_key": "requires_workshop", "equals": True},
            {"when": "step", "step_code": "diagnosis", "field_key": "aircraft_status", "equals": "grounded"},
        ],
    },
    {
        "code": "solved",
        "sort_order": 4,
        "is_initial": False,
        "is_final": True,
        "advance_rules": [{"when": "all_required"}],
    },
    {
        "code": "no_findings",
        "sort_order": 5,
        "is_initial": False,
        "is_final": True,
        "advance_rules": [
            {
                "when": "step",
                "step_code": "report",
                "field_key": "has_finding",
                "equals": False,
                "only_this_step": True,
            }
        ],
    },
]


def test_el_reporte_sin_novedad_cierra_el_ticket():
    assert (
        status_for_rules(STATUSES, {"has_finding": False}, ["report"], REQUIRED, "reported")
        == "no_findings"
    )


def test_una_novedad_no_dispara_una_regla():
    assert status_for_rules(STATUSES, {"has_finding": True}, ["report"], REQUIRED, "reported") is None


def test_el_diagnostico_detiene_la_aeronave_segun_la_regla_del_estado():
    assert (
        status_for_rules(
            STATUSES,
            {"has_finding": True, "requires_workshop": True},
            ["report", "diagnosis"],
            REQUIRED,
            "in_review",
        )
        == "grounded"
    )


def test_soltar_la_aeronave_vuelve_al_estado_en_curso():
    assert (
        status_for_rules(
            STATUSES,
            {"has_finding": True, "requires_workshop": False, "aircraft_status": "operational"},
            ["report", "diagnosis"],
            REQUIRED,
            "grounded",
        )
        == "in_review"
    )


def test_completar_los_pasos_usa_la_regla_de_cierre():
    assert status_for_rules(STATUSES, {"has_finding": True}, REQUIRED, REQUIRED, "grounded") == "solved"


def test_un_tipo_sin_reglas_no_mueve_el_estado():
    plain = [{**status, "advance_rules": None} for status in STATUSES]
    assert status_for_rules(plain, {"has_finding": False}, REQUIRED, REQUIRED, "reported") is None
