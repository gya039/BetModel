#!/usr/bin/env bash
# Diamond Edge — one-command deploy to Firebase Hosting
# Usage: bash deploy.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "  ⬡ Diamond Edge — Deploy"
echo "  ─────────────────────────────────"

# 1. Build the React app
echo "  › Building React app..."
cd "$ROOT/diamond-edge"
npm run build
cd "$ROOT"

# 2. Copy mlb data into site/ so Firebase serves it from the same origin
echo "  › Copying prediction data..."
mkdir -p "$ROOT/site/mlb"
cp -r "$ROOT/mlb/predictions" "$ROOT/site/mlb/"

# 3. Deploy to Firebase Hosting
echo "  › Deploying to Firebase..."
firebase deploy --only hosting

echo ""
echo "  ✓ Live at https://diamondedge-7c220.web.app"
echo ""
