import re

# Static map of common tech abbreviations to their full forms.
# Keys are uppercase for consistency; matching is case-insensitive.
# Order matters: longer/more specific entries must come before shorter ones
# to avoid partial replacement (e.g. CI/CD before CI and CD).
ABBREVIATION_MAP = {
    # Infrastructure & Cloud
    "CI/CD":    "Continuous Integration and Continuous Deployment",
    "CI":       "Continuous Integration",
    "CD":       "Continuous Deployment",
    "IaC":      "Infrastructure as Code",
    "AWS":      "Amazon Web Services",
    "GCP":      "Google Cloud Platform",
    "VM":       "Virtual Machine",
    "CDN":      "Content Delivery Network",
    "DNS":      "Domain Name System",
    "SSL":      "Secure Sockets Layer",
    "TLS":      "Transport Layer Security",
    "HA":       "High Availability",
    "DR":       "Disaster Recovery",
    "LB":       "Load Balancer",

    # Kubernetes & Containers
    "K8s":      "Kubernetes",
    "K3s":      "Lightweight Kubernetes",
    "EKS":      "Elastic Kubernetes Service",
    "GKE":      "Google Kubernetes Engine",
    "AKS":      "Azure Kubernetes Service",
    "ECS":      "Elastic Container Service",

    # Middleware & App Servers
    "WAS":      "WebSphere Application Server",
    "JBoss":    "JBoss Application Server",
    "MQ":       "Message Queue",

    # Databases
    "DB":       "Database",
    "RDS":      "Relational Database Service",
    "SQL":      "Structured Query Language",
    "ORM":      "Object-Relational Mapping",

    # Web & APIs
    "UI":       "User Interface",
    "UX":       "User Experience",

    # Dev & Testing
    "SDK":      "Software Development Kit",
    "IDE":      "Integrated Development Environment",
    "PR":       "Pull Request",
    "QA":       "Quality Assurance",
    "UAT":      "User Acceptance Testing",
    "ETL":      "Extract Transform Load",

    # Project / Product
    "POC":      "Proof of Concept",
    "MVP":      "Minimum Viable Product",
    "SLA":      "Service Level Agreement",
    "SLO":      "Service Level Objective",
    "BE":       "Backend",
    "FE":       "Frontend",
}


def expand_abbreviations(text: str) -> str:
    """
    Replace known abbreviations in text with their full forms.
    Uses word-boundary matching and is case-insensitive.
    Longer/more specific abbreviations are matched first.
    """
    for abbr, full in ABBREVIATION_MAP.items():
        pattern = r"\b" + re.escape(abbr) + r"\b"
        text = re.sub(pattern, full, text, flags=re.IGNORECASE)
    return text
