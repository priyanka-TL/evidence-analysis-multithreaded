#!/bin/bash

# Evidence Analysis - Workflow Manager
# Interactive menu for running preprocessing and processing tasks

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Project root directory
PROJECT_ROOT="/home/dell/workspace/EVIDENCE_ANALYSIS/evidence-analysis-multithreaded"
VENV_PATH="$PROJECT_ROOT/venv"

# Function to print colored messages
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Function to activate virtual environment
activate_venv() {
    print_info "Activating virtual environment..."
    if [ -f "$VENV_PATH/bin/activate" ]; then
        source "$VENV_PATH/bin/activate"
        print_success "Virtual environment activated"
        return 0
    else
        print_error "Virtual environment not found at $VENV_PATH"
        print_info "Creating virtual environment..."
        python3 -m venv "$VENV_PATH"
        source "$VENV_PATH/bin/activate"
        print_success "Virtual environment created and activated"
        return 0
    fi
}

# Function to install requirements
install_requirements() {
    print_info "Installing requirements..."
    cd "$PROJECT_ROOT"
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        print_success "Requirements installed successfully"
    else
        print_error "requirements.txt not found"
        return 1
    fi
}

# Function to run preprocessor
run_preprocessor() {
    print_info "Running preprocessor..."
    cd "$PROJECT_ROOT/pre-processor"
    
    echo ""
    echo "Available preprocessor scripts:"
    echo "  1) 1-pre-processor.py - Main preprocessor"
    echo "  2) 2-csv-splitter.py - CSV splitter"
    echo ""
    read -p "Select preprocessor script (1-2): " prep_choice
    
    case $prep_choice in
        1)
            print_info "Running 1-pre-processor.py..."
            python3 1-pre-processor.py
            ;;
        2)
            print_info "Running 2-csv-splitter.py..."
            python3 2-csv-splitter.py
            ;;
        *)
            print_error "Invalid choice"
            return 1
            ;;
    esac
    
    if [ $? -eq 0 ]; then
        print_success "Preprocessor completed successfully"
    else
        print_error "Preprocessor failed"
        return 1
    fi
}

# Function to run processor
run_processor() {
    print_info "Running processor..."
    cd "$PROJECT_ROOT/processor"
    
    echo ""
    echo "Available processor scripts:"
    echo "  1) 1-main-parallel-script.py - Main parallel processor"
    echo "  2) 2-merge-processed-csv.py - Merge processed CSVs"
    echo "  3) 3-invalid-url-&-custom-task-remover.py - Clean invalid data"
    echo "  4) 4-url-validator.py - Validate URLs"
    echo "  5) 5-enrollment-analytics.py - Enrollment analytics"
    echo "  6) 6-user-owned-tasks-analyzer.py - Task analyzer"
    echo ""
    read -p "Select processor script (1-6): " proc_choice
    
    case $proc_choice in
        1)
            print_info "Running 1-main-parallel-script.py..."
            python3 1-main-parallel-script.py
            ;;
        2)
            print_info "Running 2-merge-processed-csv.py..."
            python3 2-merge-processed-csv.py
            ;;
        3)
            print_info "Running 3-invalid-url-&-custom-task-remover.py..."
            python3 "3-invalid-url-&-custom-task-remover.py"
            ;;
        4)
            print_info "Running 4-url-validator.py..."
            python3 4-url-validator.py
            ;;
        5)
            print_info "Running 5-enrollment-analytics.py..."
            python3 5-enrollment-analytics.py
            ;;
        6)
            print_info "Running 6-user-owned-tasks-analyzer.py..."
            python3 6-user-owned-tasks-analyzer.py
            ;;
        *)
            print_error "Invalid choice"
            return 1
            ;;
    esac
    
    if [ $? -eq 0 ]; then
        print_success "Processor completed successfully"
    else
        print_error "Processor failed"
        return 1
    fi
}

# Function to show main menu
show_menu() {
    echo ""
    echo "=========================================="
    echo "   Evidence Analysis Workflow Manager"
    echo "=========================================="
    echo ""
    echo "  1) Setup - Activate venv & Install requirements"
    echo "  2) Run Preprocessor"
    echo "  3) Run Processor"
    echo "  4) Run Full Pipeline (Preprocessor → Processor)"
    echo "  5) Database Setup"
    echo "  6) Test Database Connection"
    echo "  0) Exit"
    echo ""
    echo "=========================================="
}

# Function for database setup
database_setup() {
    print_info "Database Setup Menu..."
    cd "$PROJECT_ROOT"
    
    echo ""
    echo "Database Operations:"
    echo "  1) Run all migrations"
    echo "  2) Run schema migration only"
    echo "  3) Run seed data only"
    echo "  4) Test connection"
    echo ""
    read -p "Select option (1-4): " db_choice
    
    case $db_choice in
        1)
            print_info "Running all migrations..."
            psql -d evidence_analysis -f database/migrations/001_create_schema.sql
            psql -d evidence_analysis -f database/migrations/002_seed_data.sql
            print_success "Migrations completed"
            ;;
        2)
            print_info "Running schema migration..."
            psql -d evidence_analysis -f database/migrations/001_create_schema.sql
            print_success "Schema migration completed"
            ;;
        3)
            print_info "Running seed data migration..."
            psql -d evidence_analysis -f database/migrations/002_seed_data.sql
            print_success "Seed data loaded"
            ;;
        4)
            print_info "Testing database connection..."
            python3 database/connection.py
            ;;
        *)
            print_error "Invalid choice"
            return 1
            ;;
    esac
}

# Function to test database connection
test_database() {
    print_info "Testing database connection..."
    cd "$PROJECT_ROOT"
    python3 database/connection.py
}

# Main script logic
main() {
    cd "$PROJECT_ROOT"
    
    while true; do
        show_menu
        read -p "Select an option (0-6): " choice
        
        case $choice in
            1)
                echo ""
                print_info "=== Setup: Activating venv & Installing requirements ==="
                activate_venv
                install_requirements
                print_success "Setup completed!"
                ;;
            2)
                echo ""
                print_info "=== Running Preprocessor ==="
                activate_venv
                run_preprocessor
                ;;
            3)
                echo ""
                print_info "=== Running Processor ==="
                activate_venv
                run_processor
                ;;
            4)
                echo ""
                print_info "=== Running Full Pipeline ==="
                activate_venv
                run_preprocessor
                if [ $? -eq 0 ]; then
                    run_processor
                else
                    print_error "Pipeline stopped due to preprocessor failure"
                fi
                ;;
            5)
                echo ""
                print_info "=== Database Setup ==="
                activate_venv
                database_setup
                ;;
            6)
                echo ""
                print_info "=== Testing Database Connection ==="
                activate_venv
                test_database
                ;;
            0)
                print_info "Exiting..."
                exit 0
                ;;
            *)
                print_error "Invalid option. Please select 0-6."
                ;;
        esac
        
        echo ""
        read -p "Press Enter to continue..."
    done
}

# Run main function
main
