"""pages — split from import_export.py."""
from ._common import *  # noqa

@import_export_bp.route('/')
@login_required
def import_export_page():
    # ===== PANDAS DEPENDENCY CHECK =====
    if not _validate_pandas_installed():
        flash(
            'CRITICAL: pandas library is not installed. '
            'Run: pip install pandas>=2.3.3. '
            'Import/export functionality is disabled.',
            'danger'
        )
        return render_template('import_export_new.html', pandas_unavailable=True)
    # ===== END DEPENDENCY CHECK =====
    
    full_raw_import_enabled = str(
        os.environ.get('FULL_RAW_IMPORT_ENABLED', current_app.config.get('FULL_RAW_IMPORT_ENABLED', '0'))
    ).strip().lower() in ['1', 'true', 'on', 'yes']
    report_name = request.args.get('full_raw_import_report') or session.get('full_raw_import_report')
    report_meta = session.get('full_raw_import_report_meta')
    if report_name and report_meta and report_meta.get('name') == report_name:
        full_raw_import_report = report_meta
    elif report_name:
        full_raw_import_report = {'name': report_name, 'created_at': None}
    else:
        full_raw_import_report = None

    full_db_sync_enabled = str(
        os.environ.get('FULL_DB_SYNC_ENABLED', current_app.config.get('FULL_DB_SYNC_ENABLED', '1'))
    ).strip().lower() in ['1', 'true', 'on', 'yes']
    full_db_report_name = (request.args.get('full_db_import_report')
                           or session.get('full_db_import_report'))
    full_db_report_meta = session.get('full_db_import_report_meta')
    if full_db_report_name and full_db_report_meta and full_db_report_meta.get('name') == full_db_report_name:
        full_db_import_report = full_db_report_meta
    elif full_db_report_name:
        full_db_import_report = {'name': full_db_report_name, 'created_at': None}
    else:
        full_db_import_report = None
    tenants = []
    export_modules = [
        {'key': key, 'label': cfg['label'], 'tables': cfg['tables']}
        for key, cfg in EXPORT_MODULES.items()
    ]
    return render_template(
        'import_export_new.html',
        full_raw_import_enabled=full_raw_import_enabled,
        full_raw_import_report=full_raw_import_report,
        tenants=tenants,
        export_modules=export_modules,
        full_db_sync_enabled=full_db_sync_enabled,
        full_db_import_report=full_db_import_report,
    )

