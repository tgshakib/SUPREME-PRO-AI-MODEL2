from flask import Flask, send_file
import os

app = Flask(__name__)

ZIP_PATH = os.path.join(os.path.dirname(__file__), 'mybot_backup.zip')


@app.route('/')
def download():
    return send_file(
        ZIP_PATH,
        as_attachment=True,
        download_name='SUPREME_PRO_AI_BOT.zip',
        mimetype='application/zip',
    )


@app.route('/health')
def health():
    return {'status': 'ok', 'zip_exists': os.path.exists(ZIP_PATH)}, 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
