package empfactory;

public class FullTime extends Employee {
	private double annualSal;

	public FullTime() {
		super();
		annualSal=0.0;
	}

	public FullTime(char type, String staffIdPar, String namePar, double annualSalPar) {
		super(type,staffIdPar, namePar);
		this.annualSal=annualSalPar;
		// TODO Auto-generated constructor stub
	}

	@Override
	public double calcMonthlyPay() {		
		return (annualSal/12);
	}
	
	public String toString() {
		return  super.toString()+
				"\nAnnual Salary=" + annualSal ;
	}

	
}
