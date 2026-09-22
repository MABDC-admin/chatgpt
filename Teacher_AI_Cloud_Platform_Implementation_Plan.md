# Teacher AI Cloud Platform (ChatGPT-like AI System)

## Text AI + Image Generation + School Knowledge AI + RBAC + Credit Management

## Project Vision

Build a private AI platform for schools where teachers, administrators,
and students can use AI tools through a controlled environment.

The system provides:

-   Teacher AI Assistant
-   School Knowledge AI
-   AI Management Platform

## System Architecture

    USERS
     |
    Next.js Web Application
     |
    AI Gateway / Backend API
     |
    Chat Engine | Image Engine | RAG Engine | Admin Engine
     |
    PostgreSQL + pgvector + Storage

## Technology Stack

### Frontend

-   Next.js + React
-   ChatGPT-style interface
-   Streaming messages
-   Image preview
-   Dashboard

### Backend

Recommended: - FastAPI (Python) or NestJS (Node.js)

Responsibilities: - Authentication - OpenAI API connection - Prompt
routing - Credit checking - Usage tracking - File processing

### Database

PostgreSQL stores: - Users - Roles - Permissions - Conversations -
Messages - Usage logs - Credits - Files

### AI Memory

pgvector enables: - School knowledge base - Document search - RAG
workflows

## AI Engine Design

### Text AI Engine

Model: - GPT-4o / latest GPT model

Functions: - Chat - Lesson planning - Writing - Analysis - Document
processing

### Image AI Engine

Teacher default: - gpt-image-1

Used for: - Educational posters - Infographics - Science illustrations -
History visuals - Classroom materials

### Cost Optimization Router

    User Request
     |
    AI Classifier
     |
    Text -> GPT Model
    Image -> Image Router
              |
              gpt-image-1 / gpt-image-1-mini

## RBAC User Management

Roles:

-   SUPER ADMIN
-   ADMIN
-   PRINCIPAL
-   REGISTRAR
-   TEACHER
-   STUDENT
-   PARENT
-   IT ADMIN

Permissions control: - AI access - User management - Reports - System
settings

## AI Credit Management

Track:

-   Text usage
-   Image generation
-   Token consumption
-   Cost per user

Example:

Teacher Account: - Monthly AI allocation - Usage monitoring - Remaining
balance

## Admin Dashboard

Features:

-   User management
-   AI usage analytics
-   Cost reports
-   Credit allocation
-   Activity logs

## Implementation Phases

### Phase 1: Foundation Setup

-   Server setup
-   Docker environment
-   PostgreSQL
-   Redis
-   Backend API
-   Authentication
-   RBAC

### Phase 2: ChatGPT Core

-   Chat interface
-   Streaming responses
-   Conversation history
-   Model selection

### Phase 3: Teacher AI Tools

-   Lesson planner
-   Assessment generator
-   PPT assistant
-   Worksheet creator

### Phase 4: Image Generation System

-   Prompt analyzer
-   gpt-image-1 integration
-   Image history
-   Gallery
-   Credit deduction

### Phase 5: School Knowledge AI

-   PDF/DOCX/PPTX upload
-   Text extraction
-   Embeddings
-   pgvector search
-   AI answers using school documents

### Phase 6: Admin Platform

-   User management
-   Credit management
-   Usage reports

### Phase 7: Deployment

Production environment: - Ubuntu Server - Docker - Nginx - PostgreSQL -
Redis - Cloudflare Tunnel - SSL - Backup system

## Security

Implement:

-   JWT authentication
-   Password hashing
-   Audit logs
-   Rate limiting
-   Role permissions
-   Secure API key storage

## Future Expansion

AI Agents:

-   Registrar Agent
-   Teacher Agent
-   Principal Agent

## Final Target

A private ChatGPT Enterprise-style education AI platform:

-   Teacher AI
-   Student AI
-   Admin AI
-   School Knowledge AI
-   OpenAI Models
-   PostgreSQL
-   pgvector
-   RBAC
-   Usage Dashboard

## Recommended Build Priority

1.  Authentication + RBAC
2.  ChatGPT-style interface
3.  GPT text assistant
4.  gpt-image-1 teacher image generation
5.  PostgreSQL usage tracking
6.  Credit dashboard
7.  pgvector knowledge base
8.  AI agents and automation
