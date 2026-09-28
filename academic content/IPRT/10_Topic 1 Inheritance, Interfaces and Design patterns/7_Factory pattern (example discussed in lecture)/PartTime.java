package empfactory;

public class PartTime extends Employee{
	private int numHours;

	public PartTime() {
		super();
		this.numHours = 0;
	}
	public PartTime(char type, String staffIdPar, String name, int numHours) {
		super(type, staffIdPar, name);
		this.numHours = numHours;
	}
	public double calcMonthlyPay() {
		return numHours*80;		
	}
	@Override
	public String toString() {
		return  super.toString()+
				"\nNumer of hours=" + numHours + 
				"\tMonthly Pay (at R80/hour)="+ calcMonthlyPay();
	}
	

}
