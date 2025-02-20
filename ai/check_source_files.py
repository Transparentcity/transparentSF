import os
import glob
from pathlib import Path

def check_source_files():
    """Check for markdown files in the output directories."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'output')
    
    timeframes = ['annual', 'monthly', 'daily']
    locations = ['citywide'] + [f'district_{i}' for i in range(1, 12)]
    
    print("\nChecking for source markdown files:")
    print("-" * 50)
    
    for timeframe in timeframes:
        print(f"\nTimeframe: {timeframe}")
        print("-" * 30)
        
        # Check citywide
        citywide_path = os.path.join(output_dir, timeframe)
        if os.path.exists(citywide_path):
            md_files = glob.glob(os.path.join(citywide_path, '*.md'))
            print(f"\nCitywide directory: {citywide_path}")
            print(f"MD files found: {len(md_files)}")
            if md_files:
                print("Sample files:")
                for f in md_files[:3]:  # Show first 3 files
                    print(f"  - {os.path.basename(f)}")
        
        # Check districts
        districts_path = os.path.join(output_dir, timeframe, 'districts')
        if os.path.exists(districts_path):
            for district in [f'district_{i}' for i in range(1, 12)]:
                district_path = os.path.join(districts_path, district)
                if os.path.exists(district_path):
                    md_files = glob.glob(os.path.join(district_path, '*.md'))
                    print(f"\n{district}:")
                    print(f"Directory: {district_path}")
                    print(f"MD files found: {len(md_files)}")
                    if md_files:
                        print("Sample files:")
                        for f in md_files[:3]:  # Show first 3 files
                            print(f"  - {os.path.basename(f)}")
                else:
                    print(f"\n{district}: Directory does not exist")
        else:
            print(f"\nNo districts directory found for {timeframe}")
    
    print("\n" + "-" * 50)

if __name__ == "__main__":
    check_source_files() 