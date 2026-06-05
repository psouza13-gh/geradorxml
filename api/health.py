from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})
