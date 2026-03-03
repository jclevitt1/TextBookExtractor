#!/bin/bash
# Deploy script for Textbook Extraction Pipeline

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load .env file
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create a .env file with ANTHROPIC_API_KEY"
    exit 1
fi

source .env

# Check if ANTHROPIC_API_KEY is set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}Error: ANTHROPIC_API_KEY not set in .env${NC}"
    exit 1
fi

# Default to dev stage
STAGE="${1:-dev}"

echo -e "${YELLOW}Building SAM application...${NC}"
sam build

echo -e "${YELLOW}Deploying to stage: ${STAGE}${NC}"
sam deploy \
  --stack-name textbook-extraction-${STAGE} \
  --parameter-overrides Stage=${STAGE} AnthropicApiKey=${ANTHROPIC_API_KEY} \
  --resolve-s3 --resolve-image-repos \
  --capabilities CAPABILITY_IAM \
  --region us-west-1

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Deployment successful!${NC}"
    echo ""
    echo "Get the state machine ARN:"
    echo "  aws cloudformation describe-stacks \\"
    echo "    --stack-name textbook-extraction-${STAGE} \\"
    echo "    --region us-west-1 \\"
    echo "    --query 'Stacks[0].Outputs[?OutputKey==\`StateMachineArn\`].OutputValue' \\"
    echo "    --output text"
else
    echo -e "${RED}❌ Deployment failed${NC}"
    exit 1
fi
